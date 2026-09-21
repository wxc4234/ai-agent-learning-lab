"""API 输入与输出契约：同时提供运行时校验和 Swagger 文档。"""

import unicodedata
from typing import Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


def normalize_login_username(value: str) -> str:
    """注册与登录共用的用户名规范。"""
    normalized = value.strip()
    valid_characters = all(
        (character.isascii() and (character.isalnum() or character == "_"))
        or unicodedata.name(character, "").startswith("CJK UNIFIED IDEOGRAPH-")
        or character == "〇"
        for character in normalized
    )
    if not 3 <= len(normalized) <= 64 or not valid_characters:
        raise ValueError("用户名必须为 3～64 个汉字、英文字母、数字或下划线")

    return normalized.lower()


class ChatRequest(BaseModel):
    """发起一轮聊天所需的会话标识和用户问题。"""

    session_id: str
    prompt: str


class ChatResponse(BaseModel):
    """当前非流式聊天接口返回的最小结果。"""

    reply: str


class ToolTestRequest(BaseModel):
    """工具调用演示接口的用户提示。"""

    prompt: str


class ConversationMessage(BaseModel):
    """一条已持久化的对话消息"""

    role: Literal["user", "assistant"]
    content: str


class ConversationHistoryResponse(BaseModel):
    """一个会话的完整历史记录。"""

    session_id: str
    total: int
    messages: list[ConversationMessage]


class ConversationExecutionStatusResponse(BaseModel):
    """会话执行占用的公开快照，不包含释放凭证。"""

    session_id: str = Field(
        description="当前查询的会话标识",
    )

    occupied: bool = Field(
        description="查询时是否存在执行占用，不代表执行进程一定存活",
    )

    # 必须显式返回该字段；没有占用时返回 JSON null。
    # 时间仅供观察，不能据此自动过期或强制释放。
    acquired_at: datetime | None = Field(
        description="占用获取时间；没有占用时为 null",
    )


class CancelRunRequest(BaseModel):
    """请求停止一次运行时携带的原因。"""

    reason: Literal["user", "timeout"]


class RegisterRequest(BaseModel):
    """注册输入：规范用户名，拒绝包含空白字符的密码。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    username: str = Field(
        description="3～64 个汉字、英文字母、数字或下划线，忽略首尾空白和英文大小写",
    )

    password: SecretStr = Field(
        min_length=8,
        max_length=128,
        description="8～128 个字符，不允许空白字符，区分大小写",
    )

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return normalize_login_username(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()

        if any(character.isspace() for character in password):
            raise ValueError("密码不能包含空格或其他空白字符")

        return value


class RegisterResponse(BaseModel):
    """注册成功：只输出客户端需要的身份字段。"""

    external_id: str
    username: str


class RegistrationErrorResponse(BaseModel):
    """注册失败：只输出明确允许公开的错误信息。"""

    code: str
    message: str


class LoginRequest(BaseModel):
    """登录输入：规范用户名，原样保留待验证密码。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    username: str = Field(description="注册时使用的用户名，忽略首尾空白和大小写")

    password: SecretStr = Field(
        min_length=1,
        max_length=128,
        description="待验证的原始密码，不自动去除空白或改变大小写",
    )

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return normalize_login_username(value)


class LoginResponse(BaseModel):
    """登录成功正文：仅返回客户端需要的安全身份。"""

    external_id: str
    username: str


class LoginErrorResponse(BaseModel):
    """登录失败正文：不携带输入值、凭证或内部异常详情。"""

    code: str
    message: str

class CurrentUserResponse(BaseModel):
    """当前登录用户的安全身份。"""

    external_id: str
    username: str


class CurrentUserErrorResponse(BaseModel):
    """当前用户查询的安全错误响应。"""

    code: str
    message: str

class LogoutErrorResponse(BaseModel):
    """登出失败的安全响应，不包含令牌或内部异常详情。"""

    code: str
    message: str


class WorkspaceCreateRequest(BaseModel):
    """创建输入只接收名称，身份由登录依赖提供。"""

    # 禁止隐式类型转换和额外字段，拒绝客户端传入 user_id 等身份信息。
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    # 此处检查类型；strip 和长度规则继续由既有仓储统一处理。
    name: str = Field(
        description="工作空间名称，去除首尾空白后为 1～100 个字符",
    )


class WorkspaceResponse(BaseModel):
    """只输出客户端需要的工作空间字段。"""

    # 使用对外标识，不输出数据库内部 id 或 user_id。
    external_id: str
    name: str
    created_at: datetime

class WorkspaceListResponse(BaseModel):
    """工作空间列表，只返回公开字段与是否还有更多记录。"""

    items: list[WorkspaceResponse]

    # 表示本次返回范围之外仍有记录，不代表已经实现翻页。
    has_more: bool

class WorkspaceErrorResponse(BaseModel):
    """错误正文只包含稳定错误码和安全提示。"""

    code: str
    message: str

class WorkspaceDirectoryRequest(BaseModel):
    """目录绑定只接受路径，身份由服务端提供。"""

    # 拒绝隐式类型转换和额外字段，不能让客户端指定资源归属。
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    # 这里只检查字符串类型与非空；实际目录规则复用校验服务。
    # 不 strip，避免改变包含合法空格的目录名称。
    root_path: str = Field(min_length=1)


class WorkspaceDirectoryResponse(BaseModel):
    """返回本地工作台需要的绑定结果，不暴露内部用户主键。"""

    external_id: str
    name: str
    root_path: str

class WorkspaceDirectoryStateResponse(BaseModel):
    """读取数据库保存的绑定状态，不代表目录当前一定可访问。"""

    external_id: str
    name: str

    # NULL 明确表示未绑定；字段必须出现，不能用遗漏字段表达状态。
    root_path: str | None

class TaskCreateRequest(BaseModel):
    """接收创建内容和可选请求键，归属与资源标识由服务端确定。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    # HTTP 层检查类型；标题规范化和长度规则由事务服务统一处理。
    title: str = Field(
        description="任务标题，去除首尾空白后须为 1～200 个字符",
    )

    # 暂时兼容只发送标题的 BFF；缺省或 null 时不启用幂等。
    # 同一次创建的重试必须复用原键，不能每次请求重新生成。
    # 不接受客户端指纹，摘要始终由服务端根据规范化内容计算。
    request_key: str | None = Field(
        default=None,
        min_length=32,
        max_length=32,
        pattern=r"^[0-9a-f]{32}$",
        description=(
            "可选的 32 位小写十六进制创建请求键；"
            "同一次创建重试须复用，缺省或 null 时不启用幂等"
        ),
    )


class TaskResponse(BaseModel):
    """任务创建结果，不暴露内部主键或 ORM 对象。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
    )

    # 三个标识均为服务端生成的对外标识。
    external_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    workspace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    conversation_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    title: str = Field(min_length=1, max_length=200)
    created_at: datetime

class TaskDetailResponse(BaseModel):
    """提供恢复任务所需的项目资料与任务标识。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
    )

    # 项目资料也来自服务端，不从 URL 中信任名称或归属。
    workspace: WorkspaceResponse
    task: TaskResponse

class FileEditProposalDetailResponse(BaseModel):
    """提案审阅详情，不暴露宿主目录、内部主键或完整待写正文。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
    )

    # 标识用于前端核对当前查看对象，不作为授权凭据。
    proposal_id: str = Field(
        min_length=32,
        max_length=32,
        pattern=r"^[0-9a-f]{32}$",
    )
    workspace_id: str = Field(
        min_length=32,
        max_length=32,
        pattern=r"^[0-9a-f]{32}$",
    )
    task_id: str = Field(
        min_length=32,
        max_length=32,
        pattern=r"^[0-9a-f]{32}$",
    )

    relative_path: str = Field(
        min_length=1,
        max_length=4096,
    )

    # 当前数据库只支持pending，未来扩展状态时同步修改响应契约。
    status: Literal["pending"]

    baseline_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    proposed_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    # Diff来自保存记录；截断时不能声称已经完整审阅。
    diff: str = Field(
        min_length=1,
        max_length=16384,
    )
    diff_truncated: bool
    created_at: datetime

class TaskRunItemResponse(BaseModel):
    """任务运行列表中的单条概要，不包含事件正文。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
    )

    # 沿用现有 /runs/{run_id} 的整数标识，便于后续读取详情。
    # 标识只负责定位，不能替代资源授权。
    run_id: int = Field(gt=0)

    # 保留数据库中的真实状态，不把未知状态擅自映射为终态。
    status: str = Field(min_length=1)

    started_at: datetime
    finished_at: datetime | None

    # 没有结束时间时返回 None，不伪造最终耗时。
    duration_ms: int | None = Field(ge=0)


class TaskRunListResponse(BaseModel):
    """指定任务的运行列表，以及下一页游标。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
    )

    # 返回定位信息，后续 BFF 可以校验响应是否对应请求的任务。
    workspace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    task_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    items: list[TaskRunItemResponse]

    # 取本页最后一条记录的 Run ID；没有下一页时明确返回 None。
    next_cursor: str | None
