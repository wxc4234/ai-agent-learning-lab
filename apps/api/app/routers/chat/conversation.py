import asyncio

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from app.services.runtime.execution.execution_recovery import recover_conversation_execution

from app.dependencies import CurrentUser
from app.repositories.chat.conversation_repository import load_conversation
from app.routers.chat.chat_boundary import ChatRoute
from app.schemas import (
    ConversationExecutionStatusResponse,
    ConversationHistoryResponse,
    ConversationMessage,
)
from app.services.runtime.execution.conversation_execution_query import (
    get_conversation_execution_status,
)

# 该接口服务于后续的会话列表、刷新恢复和 Agent 运行历史界面。
router = APIRouter(
    tags=["conversations"],
    route_class=ChatRoute,
)


@router.get(
    "/sessions/{session_id}/messages",
    response_model=ConversationHistoryResponse,
)
def get_session_message(
    session_id: str,
    current_user: CurrentUser,
) -> ConversationHistoryResponse:
    messages = load_conversation(
        user_id=current_user.id,
        session_id=session_id,
    )

    return ConversationHistoryResponse(
        session_id=session_id,
        total=len(messages),
        messages=[ConversationMessage.model_validate(message) for message in messages],
    )


@router.get(
    "/sessions/{session_id}/execution",
    response_model=ConversationExecutionStatusResponse,
)
async def get_session_execution(
    session_id: str,
    current_user: CurrentUser,
) -> ConversationExecutionStatusResponse:
    # 用户身份来自服务端依赖，不接受客户端传入 user_id。
    # 同步数据库查询在线程内执行，避免阻塞事件循环。
    # 查询服务在线程内部创建、关闭 Session，不跨线程传递数据库会话。
    status = await asyncio.to_thread(
        get_conversation_execution_status,
        user_id=current_user.id,
        session_id=session_id,
    )

    # 显式转换公开字段，避免将内部数据对象直接作为接口契约。
    # 此处不获取或释放占用，也不创建 Run。
    return ConversationExecutionStatusResponse(
        session_id=status.session_id,
        occupied=status.occupied,
        acquired_at=status.acquired_at,
    )

"""会话历史查询接口。"""


class ExecutionRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post("/sessions/{session_id}/execution/recover", status_code=204)
async def recover_session_execution(
    session_id: str,
    body: ExecutionRecoveryRequest,
    current_user: CurrentUser,
) -> Response:
    # 线程独立持有事务；请求取消不意味着已提交的恢复被撤销。
    await asyncio.to_thread(recover_conversation_execution, user_id=current_user.id, session_id=session_id)
    return Response(status_code=204)
