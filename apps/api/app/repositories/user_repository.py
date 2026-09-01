"""用户数据访问：集中处理用户查询与创建。"""

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
