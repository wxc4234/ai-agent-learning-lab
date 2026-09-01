"""集中管理 SQLAlchemy 连接、会话和 ORM 基类。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# Engine 统一管理到 PostgreSQL 的连接；连接失效时先探测，避免复用坏连接。
engine = create_engine(settings.database_url, pool_pre_ping=True)

# 每次请求或独立业务操作创建自己的 Session，避免共享事务状态。
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的共同基类，继承它的类会映射为数据表。"""
