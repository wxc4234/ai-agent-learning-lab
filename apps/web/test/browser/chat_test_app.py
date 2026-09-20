"""Isolated browser server: real auth/chat/storage, deterministic model only."""

import asyncio
import os

if not os.environ.get("DATABASE_URL", "").split("?")[0].rsplit("/", 1)[-1].startswith("agent_lab_test_"):
    raise RuntimeError("Browser test app requires a generated isolated database")

from app.main import app  # noqa: F401 -- uvicorn entry point after isolation guard
from app.services.chat import chat_service
from app.routers.runtime import runs
from app.services.runtime.agent_runtime import FinalAnswer, ModelUsage, ToolAction


class BrowserDecisionMaker:
    def __init__(self, **kwargs):
        self.prompt = str(kwargs.get("messages", ""))

    async def __call__(self, observations):
        if "[readonly-" in self.prompt:
            from readonly_model import readonly_decision

            return readonly_decision(self.prompt, observations)
        if "[cancel-" in self.prompt and not observations:
            return ToolAction(
                model_usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                tool_call_id="browser-cancel-tool",
                tool_name="calculate_rectangle_area",
                arguments='{"width": 2, "height": 3}',
            )
        if "[cancel-held]" in self.prompt:
            # 占用/恢复测试由显式取消释放，避免固定20秒在慢机器上提前结束。
            await asyncio.Future()
        if "[cancel-short]" in self.prompt:
            await asyncio.sleep(2)
        elif "[cancel-test]" in self.prompt:
            await asyncio.sleep(20)
        if "[workbench-long]" in self.prompt:
            return FinalAnswer(content="\n".join(f"第 {index} 行：工作台长回复滚动验证。" for index in range(200)))
        return FinalAnswer(content="隔离模型：认证聊天成功。")


_signals = {}


async def no_external_cancellation(run_id):
    signal = _signals.setdefault(run_id, asyncio.Future())
    return await asyncio.shield(signal)


async def publish_local_cancellation(run_id, reason):
    signal = _signals.setdefault(run_id, asyncio.Future())
    if not signal.done():
        signal.set_result(reason)


chat_service.DeepSeekDecisionMaker = BrowserDecisionMaker
chat_service.wait_for_run_cancellation = no_external_cancellation

runs.publish_run_cancellation = publish_local_cancellation

# 仅隔离浏览器测试替换系统窗口；HTTP、授权、目录校验和数据库保存均为真实实现。
if os.environ.get("BROWSER_TEST_DIRECTORY"):
    from app.routers.workspace import workspace

    _directory_picker_calls = 0

    def browser_directory_picker():
        global _directory_picker_calls
        _directory_picker_calls += 1
        if _directory_picker_calls == 1:
            return None
        return os.environ["BROWSER_TEST_DIRECTORY"]

    workspace.select_directory = browser_directory_picker


# 标题只替换模型出口，HTTP/权限/第一轮读取/条件更新均使用真实服务。
from app.services.tasks import task_workspace

async def browser_task_title(messages):
    return "自动总结的任务标题"

task_workspace.generate_title = browser_task_title
