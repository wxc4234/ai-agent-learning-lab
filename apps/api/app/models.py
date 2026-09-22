"""AI Agent 应用的 SQLAlchemy 数据模型。"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    Boolean
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    """用户是会话、消息和 Agent 运行记录的归属主体。"""

    __tablename__ = "users"

    # 保留已有用户结构：用户名与密码哈希必须同时为空或同时存在。
    __table_args__ = (
        CheckConstraint(
            "(username IS NULL AND password_hash IS NULL) OR "
            "(username IS NOT NULL AND password_hash IS NOT NULL)",
            name="ck_users_login_credentials_pair",
        ),
    )

    # 数据库内部主键，供其他表通过外键关联，不直接暴露为业务身份。
    id: Mapped[int] = mapped_column(primary_key=True)

    # 前端或未来认证系统提供的稳定用户标识，查询时也会使用索引。
    external_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)

    # 历史用户允许没有登录身份；新注册用户必须提供完整凭证
    username: Mapped[str | None] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=True,
    )
    # 保存密码服务生成的完整哈希字符串，绝不保存明文密码
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 由数据库生成创建时间，避免依赖应用服务器的本地时钟。
    create_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # 一个用户可以拥有多个会话；数据库关联字段实际保存在 Conversation.user_id。
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")

    # 一个用户可以拥有多个工作空间，归属外键保存在 Workspace.user_id。
    workspaces: Mapped[list["Workspace"]] = relationship(
        back_populates="user",
    )

class Workspace(Base):
    """用户拥有的工作空间，后续承载项目任务与代码资源。"""

    __tablename__ = "workspaces"

    # 数据库保留名称约束，并拒绝用空字符串表示目录。
    # NULL 明确表示尚未绑定；目录是否真实有效由服务层检查。
    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100",
            name="ck_workspaces_name_length",
        ),
        CheckConstraint(
            "root_path IS NULL OR char_length(root_path) > 0",
            name="ck_workspaces_root_path_not_empty",
        ),
    )

    # 内部主键用于数据库关联，对外使用独立生成的 external_id。
    id: Mapped[int] = mapped_column(primary_key=True)

    # 对外标识由服务端生成；唯一索引不能替代所有权校验。
    external_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
    )

    # 外键保证用户存在，按用户查询时可使用此索引。
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        index=True,
    )

    # 显示名称允许重名，不用作资源身份或幂等键。
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # 保存目录校验服务返回的规范绝对路径；旧工作空间保持未绑定。
    # 不设置默认目录，避免将用户尚未选择的目录关联到工作空间。
    root_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # 由数据库生成带时区的创建时间，避免依赖应用服务器时钟。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # 与 User.workspaces 配对，关系名称必须与 back_populates 一致。
    user: Mapped["User"] = relationship(
        back_populates="workspaces",
    )

    # 一个项目可以包含多个任务，实际关联字段保存在 Task.workspace_id。
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="workspace",
    )

class Task(Base):
    """项目中的一个持续工作目标，不等同于一次 Agent 执行。"""

    __tablename__ = "tasks"

    # 标题用于展示，允许重名；数据库兜底限制长度。
    # 去除首尾空白等输入规则，后续由创建服务负责。
    __table_args__ = (
        CheckConstraint(
            "char_length(title) BETWEEN 1 AND 200",
            name="ck_tasks_title_length",
        ),
    )

    # 内部主键用于表之间关联，对外使用独立的业务标识。
    id: Mapped[int] = mapped_column(primary_key=True)

    # 后续由服务端生成 UUID，不使用标题作为资源身份。
    external_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
    )

    # 每个任务必须属于一个项目；任务所有者沿 Workspace.user_id 查找。
    # 不额外复制 user_id，避免在 Task 中维护第二份项目归属。
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id"),
        index=True,
        nullable=False,
    )

    # Task 标题用于项目任务列表，允许同一项目出现同名任务。
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    # 由数据库生成带时区的创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # 与 Workspace.tasks 配对。
    workspace: Mapped["Workspace"] = relationship(
        back_populates="tasks",
    )

    # 当前设计中，一个任务至多对应一个会话。
    # 真正的一对一约束由 Conversation.task_id 的唯一索引保证。
    conversation: Mapped["Conversation | None"] = relationship(
        back_populates="task",
    )

class TaskCreationRequest(Base):
    """保存一次任务创建操作，重试时找回结果，删除后保留请求键。"""

    __tablename__ = "task_creation_requests"

    __table_args__ = (
        # 同一个用户、同一个项目中的请求键只能对应一条记录。
        # 数据库唯一约束负责兜底，不能只依赖应用层先查再插。
        UniqueConstraint(
            "user_id",
            "workspace_id",
            "request_key",
            name="uq_task_creation_requests_scope_key",
        ),
        CheckConstraint(
            "request_key ~ '^[0-9a-f]{32}$'",
            name="ck_task_creation_requests_key",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_task_creation_requests_hash",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # 身份必须来自服务端解析；请求键不是访问凭证。
    # 上面的联合唯一索引以 user_id 开头，不再重复建立单列索引。
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    # 请求键在项目内生效，不能拿其他项目的记录重放结果。
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id"),
        index=True,
        nullable=False,
    )

    # 后续由浏览器为一次创建意图生成；网络重试必须复用。
    request_key: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    # 后续由服务端对规范化请求计算 SHA-256，不信任客户端提供的摘要。
    request_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    # 删除 Task 时保留请求记录，只把关联置空。
    # NULL 表示这个键不能再返回一个有效任务，不能当成可重新创建。
    # 不增加 ORM relationship，避免 ORM 级联干预数据库的 SET NULL。
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "tasks.id",
            ondelete="SET NULL",
        ),
        index=True,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

class ConversationExecutionSlot(Base):
    """保存会话执行占用，与 Run 的业务终态分开管理。"""

    __tablename__ = "conversation_execution_slots"

    __table_args__ = (
        # 执行标记由服务端生成；格式固定，便于后续安全地匹配释放。
        CheckConstraint(
            "owner_token ~ '^[0-9a-f]{32}$'",
            name="ck_conversation_execution_slots_owner_token",
        ),
    )

    # 一个会话最多有一条占用记录，主键同时承担唯一约束。
    # 不使用级联删除：不能因为删除会话而静默清除执行占用。
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"),
        primary_key=True,
    )

    # 仅服务端诊断使用；旧记录保持 NULL，不能猜测其执行者已死亡。
    owner_host_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_pid: Mapped[int | None] = mapped_column(nullable=True)

    # 标记当前持有者，不是用户身份，也不是访问凭证。
    # 后续释放必须同时匹配 conversation_id 和 owner_token，
    # 防止旧执行的迟到清理误删新执行的占用。
    owner_token: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    # 时间只用于观察和排查，不能仅凭占用时间长就认定执行已停止。
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class LoginSession(Base):
    """某次登录的服务端记录；不保存原始会话令牌。"""

    __tablename__ = "login_sessions"

    __table_args__ = (
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_login_sessions_token_hash",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_login_sessions_expiration",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_login_sessions_revocation",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class Conversation(Base):
    """一次连续的用户与 Agent 对话。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 外键保证每个会话都明确归属某个用户，并加速按用户查询会话列表。
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # 旧会话允许暂时没有任务，不猜测它属于哪个项目。
    # 唯一索引保证同一个 Task 不会关联两个 Conversation。
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"),
        nullable=True,
        unique=True,
        index=True,
    )

    # API 和前端使用的稳定会话标识，不暴露数据库内部自增主键。
    external_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
    )

    # 标题后续可由前端填写，或由模型根据首轮消息自动生成。
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)

    create_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # 与 User.conversations 对应，便于 conversation.user 访问所属用户。
    user: Mapped[User] = relationship(back_populates="conversations")

    # 与 Task.conversation 配对；历史会话读取时可能得到 None。
    task: Mapped["Task | None"] = relationship(
        back_populates="conversation",
    )

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")

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

    # Run 单独保存执行者，覆盖创建已提交但返回 ID 丢失、占用已释放的情况。
    owner_host_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_pid: Mapped[int | None] = mapped_column(nullable=True)

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
        JSON().with_variant(JSONB, "postgresql"),
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

class FileEditProposal(Base):
    """保存确定的修改内容；创建提案不等于批准或应用修改。"""

    __tablename__ = "file_edit_proposals"

    __table_args__ = (
        # 审批决定与应用生命周期分开；已领取记录永不回到idle。
        CheckConstraint(
            "application_status IN ('idle', 'running', 'applied', 'not_applied', 'uncertain')",
            name="ck_file_edit_proposals_application_status",
        ),
        CheckConstraint(
            "(application_status = 'idle' AND application_token IS NULL) OR "
            "(application_status != 'idle' AND status = 'approved' "
            "AND application_token IS NOT NULL AND application_token ~ '^[0-9a-f]{32}$')",
            name="ck_file_edit_proposals_application_token",
        ),
        # 状态集合由数据库兜底；合法转换由持锁事务服务控制。
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_file_edit_proposals_status",
        ),
        # 截断内容不能获批，防止其他写入路径绕过服务检查。
        CheckConstraint(
            "status != 'approved' OR diff_truncated = false",
            name="ck_file_edit_proposals_approval_diff",
        ),
        CheckConstraint(
            "char_length(bound_root) BETWEEN 1 AND 4096",
            name="ck_file_edit_proposals_root",
        ),
        CheckConstraint(
            "char_length(relative_path) BETWEEN 1 AND 4096",
            name="ck_file_edit_proposals_path",
        ),
        CheckConstraint(
            "baseline_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_file_edit_proposals_baseline",
        ),
        CheckConstraint(
            "proposed_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_file_edit_proposals_proposed_hash",
        ),
        CheckConstraint(
            "octet_length(proposed_content) <= 262144",
            name="ck_file_edit_proposals_content_size",
        ),
        CheckConstraint(
            "char_length(diff) BETWEEN 1 AND 16384",
            name="ck_file_edit_proposals_diff_size",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(
        String(32),
        unique=True,
        index=True,
        nullable=False,
    )

    # 归属沿Task→Workspace查询，不重复维护用户和项目字段。
    # 任务删除时由数据库同时清理提案，包括已有决策的提案。
    # 不添加ORM反向关系，避免ORM提前将外键置空。
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # 仅供服务端核对目录绑定，不放入公开返回对象。
    bound_root: Mapped[str] = mapped_column(Text, nullable=False)
    relative_path: Mapped[str] = mapped_column(String(4096), nullable=False)

    baseline_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # 保存确定的新内容，后续不能从模型重新取一份内容来执行。
    proposed_content: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    diff: Mapped[str] = mapped_column(Text, nullable=False)
    diff_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # 创建时为pending；approved/rejected只记录决策，不表示文件已应用。
    status: Mapped[str] = mapped_column(
        String(20),
        server_default="pending",
        nullable=False,
    )
    # 令牌仅供内部执行器完成登记，不暴露给浏览器或模型。
    application_status: Mapped[str] = mapped_column(
        String(20), server_default="idle", nullable=False,
    )
    application_token: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
