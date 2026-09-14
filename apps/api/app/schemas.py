"""API 输入与输出契约：同时提供运行时校验和 Swagger 文档。"""

import unicodedata
from typing import Literal

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