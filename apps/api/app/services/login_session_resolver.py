"""解析登录令牌，返回安全身份；不签发会话或管理调用方事务。"""

import re
from datetime import UTC, datetime
from hashlib import sha256

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.repositories.login_session_repository import get_active_login_session
from app.repositories.user_repository import get_user_by_id
from app.services.authentication_service import AuthenticatedUser

class InvalidLoginSessionError(Exception):
    """缺失、无效、过期和撤销的登录凭证使用同一业务错误。"""

    code = "invalid_login_session"

    def __init__(self) -> None:
        super().__init__("登录状态无效，请重新登录")


def resolve_login_session(
    session: Session,
    token: SecretStr | None,
    *,
    now: datetime | None = None,
) -> AuthenticatedUser:
    """只读解析登录身份；Session 生命周期和事务由调用方管理。"""

    if token is None:
        raise InvalidLoginSessionError()

    raw_token = token.get_secret_value()

    if re.fullmatch(r"[A-Za-z0-9_-]{43}", raw_token) is None:
        raise InvalidLoginSessionError()

    token_hash = sha256(raw_token.encode("utf-8")).hexdigest()
    checked_at = datetime.now(UTC) if now is None else now

    with session.no_autoflush:
        login_session = get_active_login_session(
            session,
            token_hash=token_hash,
            now=checked_at
        )

        if login_session is None:
            raise InvalidLoginSessionError()

        user = get_user_by_id(session, login_session.user_id)

        if (
            user is None
            or user.username is None
            or user.password_hash is None
        ):
            raise InvalidLoginSessionError()

        return AuthenticatedUser(
            id=user.id,
            external_id=user.external_id,
            username=user.username
        )
