"""API 输入与输出契约：同时提供运行时校验和 Swagger 文档。"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


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
        description="3～64 个英文字母、数字或下划线，忽略首尾空白和大小写",
    )

    password: SecretStr = Field(
        min_length=8,
        max_length=128,
        description="8～128 个字符，不允许空白字符，区分大小写",
    )

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        normalized = value.strip()

        if re.fullmatch(r"[A-Za-z0-9_]{3,64}", normalized) is None:
            raise ValueError("用户名必须为 3～64 个英文字母、数字或下划线")

        return normalized.lower()

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()

        if any(character.isspace() for character in password):
            raise ValueError("密码不能包含空格或其他空白字符")

        return value
