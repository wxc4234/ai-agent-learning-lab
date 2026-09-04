from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.database import Base
from app.repositories.user_repository import get_or_create_user


def test_get_or_create_user_reuses_existing_user():
    engine = create_engine("sqlite://")
    # 引入模型会把 users 注册到 Base.metadata，再按模型定义创建临时测试表。
    Base.metadata.tables[models.User.__tablename__].create(engine)

    with Session(engine) as session:
        first_user = get_or_create_user(session, "test-user")
        second_user = get_or_create_user(session, "test-user")

        assert first_user.id == second_user.id
        assert second_user.external_id == "test-user"
