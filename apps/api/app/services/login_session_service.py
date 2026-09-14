"""登录会话签发：验证凭证、保存令牌摘要并提交事务。"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from hashlib import sha256
from secrets import token_urlsafe

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.repositories.login_session_repository import create_login_session
from app.schemas import LoginRequest
from app.services.authentication_service import (
    AuthenticatedUser,
    authenticate_user
)

DEFAULT_LOGIN_SESSION_TTL = timedelta(hours=8)

@dataclass(frozen=True)
class LoginSessionResult:
    """签发成功后的内部结果，原始令牌不参与 repr。"""

    user: AuthenticatedUser
    expires_at: datetime
    token: SecretStr = field(repr=False)

def issue_login_session(
    session: Session,
    request: LoginRequest,
    *,
    ttl: timedelta = DEFAULT_LOGIN_SESSION_TTL
) -> LoginSessionResult:
    """拥有签发事务；调用方负责提供并关闭无活动事务的 Session。"""

    if session.in_transaction():
        raise RuntimeError("登录会话签发服务需要无活动事务的 Session")

    if ttl <= timedelta(0):
        raise ValueError("登录会话有效期必须大于零")

    try:
        user = authenticate_user(session, request)

        token = SecretStr(token_urlsafe(32))
        token_hash = sha256(
            token.get_secret_value().encode("utf-8")
        ).hexdigest()

        created_at = datetime.now(UTC)
        expires_at = created_at + ttl

        create_login_session(
            session=session,
            user_id=user.id,
            token_hash=token_hash,
            created_at=created_at,
            expires_at=expires_at,
        )

        result = LoginSessionResult(
            user=user,
            expires_at=expires_at,
            token=token
        )

        session.commit()

    except Exception:
        session.rollback()
        raise

    return result