"""Isolated browser server: real auth/chat/storage, deterministic model only."""

import asyncio
import os

if not os.environ.get("DATABASE_URL", "").split("?")[0].rsplit("/", 1)[-1].startswith("agent_lab_test_"):
    raise RuntimeError("Browser test app requires a generated isolated database")

from app.main import app  # noqa: F401 -- uvicorn entry point after isolation guard
from app.services import chat_service
from app.services.agent_runtime import FinalAnswer


class BrowserDecisionMaker:
    def __init__(self, **kwargs):
        pass

    async def __call__(self, observations):
        return FinalAnswer(content="隔离模型：认证聊天成功。")


async def no_external_cancellation(*args):
    # No Redis traffic from browser tests; normal task cleanup cancels this await.
    await asyncio.Future()


chat_service.DeepSeekDecisionMaker = BrowserDecisionMaker
chat_service.wait_for_run_cancellation = no_external_cancellation
