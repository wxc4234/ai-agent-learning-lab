"""Isolated browser server: real auth/chat/storage, deterministic model only."""

import asyncio
import os

if not os.environ.get("DATABASE_URL", "").split("?")[0].rsplit("/", 1)[-1].startswith("agent_lab_test_"):
    raise RuntimeError("Browser test app requires a generated isolated database")

from app.main import app  # uvicorn entry point after isolation guard
from app.services.chat import chat_service
from app.routers.runtime import runs
from app.services.runtime.agent.agent_runtime import FinalAnswer, ModelUsage, ToolAction


class BrowserDecisionMaker:
    def __init__(self, **kwargs):
        self.prompt = str(kwargs.get("messages", ""))
        if "[task-sample-" in self.prompt:
            command = next(tool for tool in kwargs['tool_definitions'] if tool.name == 'run_command')
            assert '独立快照' in command.description
            assert set(command.arguments_model.model_json_schema()['properties']) == {'argv', 'working_directory'}

    async def __call__(self, observations):
        if "[layout]" in self.prompt:
            return FinalAnswer(content="布局验收完成，未修改文件。\n\n### 代码与文件\n\n- 阅读代码、搜索符号与文本\n- 审查修改提案，区分批准与应用状态\n- 查看本次运行的 Token 与耗时\n\n### 使用方式\n\n描述你要完成的任务，例如：`检查当前项目结构`。\n\n```python\nprint(\"Hello, Agent\")\n```\n\n长回复保持清晰的段落间距，代码块独立滚动。", model_usage=ModelUsage(input_tokens=20, output_tokens=30, total_tokens=50))
        if "[git-status]" in self.prompt:
            from git_status_model import git_status_decision

            return git_status_decision(observations)
        if "[task-sample-" in self.prompt:
            from task_sample_command_model import sample_command_decision

            return sample_command_decision(self.prompt, observations)
        if "[patch-apply-" in self.prompt:
            from patch_application_model import patch_application_decision

            return patch_application_decision(self.prompt, observations)
        if "[patch-proposal-" in self.prompt:
            from patch_proposal_model import patch_proposal_decision

            return patch_proposal_decision(self.prompt, observations)
        if "[proposal-" in self.prompt:
            from proposal_model import proposal_decision

            return proposal_decision(self.prompt, observations)
        if "[patch-preview-" in self.prompt:
            from patch_preview_model import patch_preview_decision

            return patch_preview_decision(self.prompt, observations)
        if "[preview-" in self.prompt:
            from preview_model import preview_decision

            return preview_decision(self.prompt, observations)
        if "[find-" in self.prompt:
            from find_model import find_decision

            return find_decision(self.prompt, observations)
        if "[command-" in self.prompt:
            from command_model import command_decision

            return command_decision(self.prompt, observations)
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
    from app.routers.workspace import directories

    _directory_picker_calls = 0

    def browser_directory_picker():
        global _directory_picker_calls
        _directory_picker_calls += 1
        if _directory_picker_calls == 1:
            return None
        return os.environ["BROWSER_TEST_DIRECTORY"]

    directories.select_directory = browser_directory_picker


# 标题只替换模型出口，HTTP/权限/第一轮读取/条件更新均使用真实服务。
from app.services.tasks import task_workspace

async def browser_task_title(messages):
    return "自动总结的任务标题"

task_workspace.generate_title = browser_task_title


if os.environ.get("BROWSER_TEST_SCRIPT") == "command-tools.mjs":
    from command_model import install_command_fixture

    install_command_fixture(app)


if os.environ.get("BROWSER_TEST_SCRIPT") == "task-sample-command.mjs":
    from task_sample_command_model import install_sample_command_fixture

    install_sample_command_fixture(app)

# 只在专属隔离浏览器场景中注入真实提交后的确认丢失。
if os.environ.get("BROWSER_TEST_SCRIPT") == "patch-proposal-tools.mjs":
    from app.tools import create_file_patch_proposal as patch_adapter

    _save_patch_proposal = patch_adapter.create_task_file_patch_proposal

    def _save_with_confirmation_fault(**kwargs):
        result = _save_patch_proposal(**kwargs)
        if kwargs["relative_path"] == "unconfirmed.txt":
            raise RuntimeError("injected confirmation loss")
        return result

    patch_adapter.create_task_file_patch_proposal = _save_with_confirmation_fault

if os.environ.get("BROWSER_TEST_SCRIPT") == "patch-application.mjs":
    from patch_application_model import install_patch_application_fixture

    install_patch_application_fixture(app)

if os.environ.get("BROWSER_TEST_SCRIPT") == "git-status.mjs":
    from git_status_model import install_git_status_fixture

    install_git_status_fixture(app)

if os.environ.get("BROWSER_TEST_SCRIPT") == "workbench-layout-wait.mjs":
    from workbench_layout_fixture import install_layout_fixture

    install_layout_fixture(app)
