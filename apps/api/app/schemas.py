"""API 输入与输出契约：同时提供运行时校验和 Swagger 文档。"""

import unicodedata
from datetime import datetime
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


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

    # 返回查询时的真实状态；approved只表示批准，不表示文件已应用。
    status: Literal["pending", "approved", "rejected"]

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

class FileEditProposalDecisionRequest(BaseModel):
    """客户端只能表达批准或拒绝，不能指定身份、正文或其他状态。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
    )

    # pending是创建状态，不是用户能够提交的决策。
    decision: Literal["approved", "rejected"]


class FileEditProposalDecisionResponse(BaseModel):
    """已提交的决策回执，不包含完整提案内容或内部主键。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
    )

    # 返回三个公开标识，供调用方核对响应属于当前操作对象。
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

    # 批准只表示记录了决策，不表示项目文件已经应用修改。
    status: Literal["approved", "rejected"]

class FileEditProposalApplicationStatusResponse(BaseModel):
    """应用记录的公开快照，不包含执行令牌或文件内容。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
    )

    # 返回定位信息，供调用方核对响应属于当前查询对象。
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

    # unknown只是执行回执中的“无法确认”，不属于持久化状态。
    # running不证明进程存活，applied不证明当前磁盘内容未变化。
    application_status: Literal[
        "idle",
        "running",
        "applied",
        "not_applied",
        "uncertain",
    ]

class FileEditProposalExecutionRequest(BaseModel):
    """显式请求尝试应用已保存的提案，不携带文件内容或执行权限。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
    )

    # 必须明确提供动作，空正文不能默认触发文件操作。
    # 这个字段只是请求意图，不证明用户身份、审批或执行范围。
    action: Literal["apply"]


class FileEditProposalExecutionResponse(BaseModel):
    """本次执行的公开回执，不等同于应用状态查询结果。"""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
    )

    # 三个公开标识用于核对响应所属资源，不包含数据库内部主键。
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

    file_status: Literal[
        "not_attempted",
        "not_replaced",
        "replaced",
        "uncertain",
    ]

    # unknown 是回执的确认状态，不是数据库中的持久化状态。
    application_status: Literal[
        "applied",
        "not_applied",
        "uncertain",
        "unknown",
    ]

    # 只接受执行器定义的公开错误码，不透传底层异常文字。
    code: Literal[
        "proposal_application_applied",
        "proposal_application_not_applied",
        "proposal_application_uncertain",
        "proposal_application_claim_unconfirmed",
        "proposal_application_registration_unconfirmed",
    ]

    # 字段必须存在；None 不能被自动补成清理成功。
    cleanup_complete: bool | None

    @model_validator(mode="after")
    def validate_result_combination(self) -> Self:
        """逐字段合法还不够，文件证据与登记结果也必须相容。"""

        # 映射的是文件证据对应的应登记终态。
        # 清理失败时，即使文件结果明确，也保留 uncertain。
        expected_outcomes = {
            ("not_attempted", None): "not_applied",
            ("not_replaced", True): "not_applied",
            ("not_replaced", False): "uncertain",
            ("replaced", True): "applied",
            ("replaced", False): "uncertain",
            ("uncertain", None): "uncertain",
            ("uncertain", True): "uncertain",
            ("uncertain", False): "uncertain",
        }

        evidence = (self.file_status, self.cleanup_complete)
        expected_outcome = expected_outcomes.get(evidence)

        if expected_outcome is None:
            raise ValueError("proposal_execution_response_invalid")

        if self.application_status == "unknown":
            if self.code == "proposal_application_claim_unconfirmed":
                # 领取没有得到确认，调用方不能进入文件替换阶段。
                if evidence != ("not_attempted", None):
                    raise ValueError("proposal_execution_response_invalid")
            elif self.code != "proposal_application_registration_unconfirmed":
                raise ValueError("proposal_execution_response_invalid")

            # 登记未确认时，保留文件证据，不能猜测数据库实际终态。
            return self

        expected_codes = {
            "applied": "proposal_application_applied",
            "not_applied": "proposal_application_not_applied",
            "uncertain": "proposal_application_uncertain",
        }

        if (
            self.application_status != expected_outcome
            or self.code != expected_codes[expected_outcome]
        ):
            raise ValueError("proposal_execution_response_invalid")

        return self


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


class TaskSampleStatusResponse(BaseModel):
    """登记状态与脱敏封锁原因快照，不包含目录/句柄或执行权限。"""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    # 固定长度同时拒绝末尾换行；标识只定位资源，归属仍由服务授权。
    workspace_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    task_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    status: Literal["missing", "busy", "sealed", "ready"]
    # 必须显式返回；非封锁状态用null，不能将清理待办混为普通可用状态。
    sealed_reason: Literal["cleanup_pending", "unavailable"] | None

    @model_validator(mode="after")
    def validate_sealed_reason(self) -> Self:
        if (self.status == "sealed") != (self.sealed_reason is not None):
            raise ValueError("封锁状态与原因必须同时出现")
        return self


class TaskSampleCleanupPreflightResponse(BaseModel):
    """来源 Task 的清理待办只读诊断快照；不授予文件操作权限。"""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    # BFF 可据此匹配当前资源；路径归属仍由服务端重新授权。
    workspace_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    task_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    # 固定分类只用于观察，不能解释为可清理、可恢复或可执行。
    result: Literal[
        "evidence_missing",
        "not_pending",
        "evidence_inconsistent",
        "directory_missing",
        "identity_unverifiable",
        "identity_matches_record",
        "inspection_unavailable",
    ]
