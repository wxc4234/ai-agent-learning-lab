"""用户数据访问：集中处理用户查询与创建。"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


def get_or_create_user(session: Session, external_id: str) -> User:
    """按业务身份获取用户；首次出现时创建，但由调用方统一提交事务。"""

    user = session.scalar(
        select(User).where(User.external_id == external_id),
    )

    if user is not None:
        return user

    # 只加入当前事务而不在这里 commit，调用方可把多项写入保持为原子操作。
    user = User(external_id=external_id)
    session.add(user)
    session.flush()

    return user


def create_registered_user(
    session: Session,
    *,
    username: str,
    password_hash: str,
) -> User:
    """创建注册用户；输入须经上层校验和哈希，由调用方提交事务。"""

    user = User(external_id=uuid4().hex, username=username, password_hash=password_hash)

    session.add(user)
    session.flush()

    return user


def get_user_by_username(
    session: Session,
    username: str,
) -> User | None:
    """按规范后的用户名查询，不存在时返回 None。"""

    return session.scalar(
        select(User).where(User.username == username),
    )
