"""API 输入与输出契约：同时提供运行时校验和 Swagger 文档。"""

from typing import Literal

from pydantic import BaseModel


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
