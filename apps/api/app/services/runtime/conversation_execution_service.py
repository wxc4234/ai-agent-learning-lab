"""会话执行占用：短事务获取与按持有者释放，不管理实际执行生命周期。"""

from dataclasses import dataclass
from datetime import datetime
import os
import re
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import ConversationExecutionSlot
from app.services.runtime.execution_process import host_identity
from app.repositories.chat.conversation_repository import (
    require_owned_conversation,
)


class ConversationBusyError(Exception):
    """会话已有执行占用，当前请求不能开始新的执行。"""

    code = "conversation_busy"

    def __init__(self) -> None:
        super().__init__("该会话仍有执行占用，请等待当前执行结束")


class InvalidExecutionOwnerTokenError(Exception):
    """释放占用时提供的持有者标记不符合格式要求。"""

    code = "invalid_execution_owner_token"

    def __init__(self) -> None:
        super().__init__("执行持有者标记须为 32 位小写十六进制字符串")


@dataclass(frozen=True)
class ConversationExecutionOwnership:
    """供服务端执行流程持有的普通数据，不依赖 ORM Session。"""

    # 会话对外标识，用于后续定位；不能替代资源授权。
    session_id: str

    # 标记本次占用的持有者，后续释放必须原样使用。
    # 该对象供服务端内部使用，不直接作为浏览器响应。
    owner_token: str

    # 使用数据库生成的占用时间，只用于观察与排查。
    acquired_at: datetime


def acquire_conversation_execution(
    session: Session,
    *,
    user_id: int,
    session_id: str,
) -> ConversationExecutionOwnership:
    """拥有一次获取事务；调用方提供并关闭独立 Session。"""

    # 必须在 try 外拒绝，不能回滚调用方已有的事务。
    if session.in_transaction():
        raise RuntimeError("执行占用服务需要无活动事务的 Session")

    try:
        # 只允许占用已经存在且可访问的会话，不隐式创建会话。
        # 本地模式复用 Task/Workspace 归属检查。
        # 会话行锁只保持到本次短事务结束，不跨模型或工具调用。
        conversation = require_owned_conversation(
            session,
            user_id=user_id,
            session_id=session_id,
            for_update=True,
        )

        # 每次成功获取属于一个新的持有者，不使用用户标识或 Run 状态代替。
        owner_token = uuid4().hex

        # 由数据库原子处理同一 conversation_id 的竞争。
        # 只忽略会话主键冲突，不吞掉外键、格式或其他数据库错误。
        statement = (
            insert(ConversationExecutionSlot)
            .values(
                conversation_id=conversation.id,
                owner_token=owner_token,
                owner_host_id=host_identity(),
                owner_pid=os.getpid(),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    ConversationExecutionSlot.conversation_id,
                ],
            )
            .returning(
                ConversationExecutionSlot.acquired_at,
            )
        )

        acquired_at = session.scalar(statement)

        if acquired_at is None:
            # 不返回其他持有者的 token，也不删除或接管已有占用。
            raise ConversationBusyError()

        # 提交前复制普通字段，避免提交后的 ORM 读取开启新事务。
        ownership = ConversationExecutionOwnership(
            session_id=conversation.external_id,
            owner_token=owner_token,
            acquired_at=acquired_at,
        )

        # 成功提交后，占用记录继续存在，但数据库事务及行锁已结束。
        session.commit()

    except Exception:
        # 失败撤销本次事务，保留权限、业务冲突和数据库异常的区别。
        session.rollback()
        raise

    return ownership


def release_conversation_execution(
    session: Session,
    *,
    user_id: int,
    session_id: str,
    owner_token: str,
) -> bool:
    """拥有一次释放事务；返回是否实际删除了匹配的占用。"""

    # 不能接管调用方事务，也不能在拒绝时回滚调用方的工作。
    if session.in_transaction():
        raise RuntimeError("执行占用服务需要无活动事务的 Session")

    try:
        # 先授权，再检查占用或执行删除。
        # 即使调用方知道 token，也不能释放其他用户的会话占用。
        # 与获取操作保持会话 → 占用记录的锁顺序。
        conversation = require_owned_conversation(
            session,
            user_id=user_id,
            session_id=session_id,
            for_update=True,
        )

        if (
            not isinstance(owner_token, str)
            or re.fullmatch(r"[0-9a-f]{32}", owner_token) is None
        ):
            raise InvalidExecutionOwnerTokenError()

        # 不采用“先读 token、再按会话删除”的两步操作。
        # 条件 DELETE 在数据库中同时匹配会话和持有者，避免误删新占用。
        statement = (
            delete(ConversationExecutionSlot)
            .where(
                ConversationExecutionSlot.conversation_id == conversation.id,
                ConversationExecutionSlot.owner_token == owner_token,
            )
            .returning(
                ConversationExecutionSlot.conversation_id,
            )
            .execution_options(synchronize_session=False)
        )

        released_conversation_id = session.scalar(statement)
        released = released_conversation_id is not None

        # 匹配不到也结束事务并释放会话行锁。
        # 不存在和属于其他持有者统一返回 False，不暴露对方 token。
        session.commit()

    except Exception:
        session.rollback()
        raise

    return released
