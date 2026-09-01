"""AI Agent 应用的 SQLAlchemy 数据模型。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    """用户是会话、消息和 Agent 运行记录的归属主体。"""

    __tablename__ = "users"

    # 数据库内部主键，供其他表通过外键关联，不直接暴露为业务身份。
    id: Mapped[int] = mapped_column(primary_key=True)

    # 前端或未来认证系统提供的稳定用户标识，查询时也会使用索引。
    external_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True
    )

    # 由数据库生成创建时间，避免依赖应用服务器的本地时钟。
    create_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # 一个用户可以拥有多个会话；数据库关联字段实际保存在 Conversation.user_id。
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user"
    )


class Conversation(Base):
    """一次连续的用户与 Agent 对话。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 外键保证每个会话都明确归属某个用户，并加速按用户查询会话列表。
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        index=True
    )

    # API 和前端使用的稳定会话标识，不暴露数据库内部自增主键。
    external_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
    )

    # 标题后续可由前端填写，或由模型根据首轮消息自动生成。
    title: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True
    )

    create_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # 与 User.conversations 对应，便于 conversation.user 访问所属用户。
    user: Mapped[User] = relationship(
        back_populates="conversations"
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation"
    )

    # 一段会话可触发多次 Agent 执行，例如每次用户发送新消息。
    runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="conversation",
    )

class Message(Base):
    """会话中的单条消息，可来自用户、助手、系统或工具。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 每条消息必须属于一个会话，按会话查询历史时可使用索引。
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"),
        index=True,
    )

    # 不使用固定枚举，便于后续接入 system、tool 等 Agent 消息角色。
    role: Mapped[str] = mapped_column(String(20))

    # 消息正文长度不固定，使用 Text 而不是限制较小的 String。
    content: Mapped[str] = mapped_column(Text)

    create_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # 与 Conversation.messages 对应，便于 message.conversation 访问所属会话。
    conversation: Mapped[Conversation] = relationship(
        back_populates="messages",
    )

class AgentRun(Base):
    """一次 Agent 执行的状态记录，不等同于一条聊天消息。"""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Run 归属某个会话，便于按会话查看 Agent 的执行历史。
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"),
        index=True,
    )

    # 后续前端可据此展示“思考中、已完成、失败”等执行状态。
    status: Mapped[str] = mapped_column(
        String(20),
        default="running",
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # 与 Conversation.runs 对应，便于 run.conversation 访问所属会话。
    conversation: Mapped[Conversation] = relationship(
        back_populates="runs",
    )

    # 一个 Run 由多个有序事件组成，例如模型调用、工具调用和异常。
    events: Mapped[list["AgentRunEvent"]] = relationship(
        back_populates="run",
    )

class AgentRunEvent(Base):
    """Agent Run 内发生的一条可追踪事件。"""

    __tablename__ = "agent_run_events"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 每个事件只属于一次 Agent 执行。
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id"),
        index=True,
    )

    # 用字符串支持持续扩展事件类型，不必频繁修改数据库枚举。
    event_type: Mapped[str] = mapped_column(
        String(50),
        index=True,
    )

    # JSONB 可保存不同事件各自的结构化数据，并支持 PostgreSQL 内部查询。
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        default=dict,
    )

    create_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # 与 AgentRun.events 对应，便于 event.run 访问所属执行。
    run: Mapped[AgentRun] = relationship(
        back_populates="events",
    )
