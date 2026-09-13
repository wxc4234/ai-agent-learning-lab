"""注册业务：编排密码哈希、用户写入与事务结果。"""

from dataclasses import dataclass

from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repositories.user_repository import create_registered_user
from app.schemas import RegisterRequest
from app.services.password_service import hash_password


class UsernameAlreadyExistsError(Exception):
    """用户名已被占用；只携带稳定的业务信息。"""

    code = "username_already_exists"

    def __init__(self) -> None:
        super().__init__("用户名已被占用")


@dataclass(frozen=True)
class RegistrationResult:
    """注册成功后的最小结果，不携带密码或密码哈希。"""

    id: int
    external_id: str
    username: str


def _is_username_conflict(error: IntegrityError) -> bool:
    """仅识别 PostgreSQL users 表的用户名唯一性冲突。"""

    original = error.orig

    return (
        isinstance(original, UniqueViolation)
        and original.sqlstate == "23505"
        and original.diag.table_name == "users"
        and original.diag.constraint_name == "ix_users_username"
    )


def register_user(session: Session, request: RegisterRequest) -> RegistrationResult:
    """拥有本次注册的事务；调用方须提供无活动事务的 Session。"""

    if session.in_transaction():
        raise RuntimeError("注册服务需要无活动事务的 Session")

    try:
        password_hash = hash_password(
            request.password.get_secret_value(),
        )
        user = create_registered_user(
            session=session,
            username=request.username,
            password_hash=password_hash,
        )

        result = RegistrationResult(
            id=user.id,
            external_id=user.external_id,
            username=request.username,
        )
        session.commit()
    except IntegrityError as error:
        session.rollback()

        if _is_username_conflict(error):
            raise UsernameAlreadyExistsError() from None
        raise
    except Exception:
        session.rollback()
        raise

    return result
