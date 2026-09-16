"""集中管理 SQLAlchemy 连接、会话和 ORM 基类。"""

from pathlib import Path

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# Engine 统一管理到 PostgreSQL 的连接；连接失效时先探测，避免复用坏连接。
engine = create_engine(settings.database_url, pool_pre_ping=True)

# 每次请求或独立业务操作创建自己的 Session，避免共享事务状态。
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的共同基类，继承它的类会映射为数据表。"""


class DatabaseNotReadyError(RuntimeError):
    """数据库不可访问或迁移版本不符合当前应用要求。"""


def check_database_ready() -> None:
    """只读检查连接与版本；结构变更只能通过显式 Alembic 迁移执行。"""
    scripts = ScriptDirectory(dir=str(Path(__file__).resolve().parents[1] / "migrations"))
    expected_heads = set(scripts.get_heads())
    if not expected_heads:
        raise DatabaseNotReadyError("应用缺少数据库迁移记录，请检查迁移文件是否完整")
    try:
        # 独立连接退出时结束读取事务，不建表、不写版本、不 commit。
        with engine.connect() as connection:
            current_heads = set(MigrationContext.configure(connection).get_current_heads())
    except SQLAlchemyError:
        raise DatabaseNotReadyError("数据库连接或版本读取失败，请检查数据库服务和连接配置") from None
    if current_heads != expected_heads:
        raise DatabaseNotReadyError(
            "数据库迁移版本与当前应用不一致；请先确认目标数据库，"
            "再进入 apps/api 执行 ../../.venv/bin/python -m alembic upgrade head"
        )
