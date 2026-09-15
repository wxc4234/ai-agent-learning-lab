"""验证登录凭证，返回安全身份；不创建登录会话。"""

from dataclasses import dataclass
from secrets import token_urlsafe

from sqlalchemy.orm import Session

from app.repositories.auth.user_repository import get_user_by_username
from app.schemas import LoginRequest
from app.services.auth.password_service import hash_password, verify_password

# 每个进程加载时生成一次，未知用户仍执行一次验证。
_dummy_password_hash = hash_password(token_urlsafe(32))


class InvalidCredentialsError(Exception):
    """未知用户名和错误密码使用相同的业务错误。"""

    code = "invalid_credentials"

    def __init__(self) -> None:
        super().__init__("用户名或密码错误")


@dataclass(frozen=True)
class AuthenticatedUser:
    """凭证验证通过后的内部身份结果，不包含密码或哈希"""

    id: int
    external_id: str
    username: str


def authenticate_user(session: Session, request: LoginRequest) -> AuthenticatedUser:
    """只查询并验证凭证；Session 生命周期由调用方管理。"""

    with session.no_autoflush:
        user = get_user_by_username(session, request.username)

    password = request.password.get_secret_value()

    if user is None or user.username is None or user.password_hash is None:
        verify_password(password, _dummy_password_hash)
        raise InvalidCredentialsError()

    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError()

    return AuthenticatedUser(
        id=user.id, external_id=user.external_id, username=user.username
    )
