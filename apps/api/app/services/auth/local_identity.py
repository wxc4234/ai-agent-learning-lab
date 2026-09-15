"""本机身份：稳定归属、并发初始化，不接管已有账号的数据。"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import User
from app.services.auth.authentication_service import AuthenticatedUser

LOCAL_USER_ID = "local-owner-v1"


def resolve_local_identity(session: Session) -> AuthenticatedUser:
    """独立事务内创建或读取本机用户，返回不依赖 Session 的快照。"""
    if session.in_transaction():
        raise RuntimeError("Local identity requires a fresh session")
    with session.begin():
        # 唯一键解决并发首次访问；本机用户没有密码，也不能网页登录。
        session.execute(
            insert(User).values(external_id=LOCAL_USER_ID)
            .on_conflict_do_nothing(index_elements=[User.external_id])
        )
        user = session.scalar(select(User).where(User.external_id == LOCAL_USER_ID))
        if user is None or user.username is not None or user.password_hash is not None:
            raise RuntimeError("Invalid local identity")
        result = AuthenticatedUser(
            id=user.id, external_id=user.external_id, username="本机用户"
        )
    return result
