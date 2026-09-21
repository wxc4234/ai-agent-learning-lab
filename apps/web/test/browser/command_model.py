"""浏览器命令验收：仅替换模型决策，命令和Docker链路真实执行。"""

import json
from contextlib import asynccontextmanager
from pathlib import Path

from app.services.runtime.agent.agent_runtime import FinalAnswer, ModelUsage, ToolAction, ToolErrorObservation


def command_decision(prompt, observations):
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    if observations:
        observation = observations[-1]
        if isinstance(observation, ToolErrorObservation):
            answer = f"命令错误：{observation.details}"
        else:
            result = json.loads(observation.result)
            answer = f"命令完成：退出码{result['exit_code']}，截断{str(result['stdout_truncated']).lower()}"
        return FinalAnswer(content=answer, model_usage=usage)
    program = "print('BROWSER_COMMAND_OK')"
    if "[command-nonzero]" in prompt:
        program += "; raise SystemExit(7)"
    elif "[command-large]" in prompt:
        program = "import os; os.write(1,b'X'*100000)"
    elif "[command-cancel]" in prompt:
        program = "import time; print('READY',flush=True); time.sleep(60)"
    argv = ["/usr/local/bin/python", "-c", program]
    if "[command-rejected]" in prompt:
        argv = ["relative-program"]
    return ToolAction(tool_call_id="browser-command", tool_name="run_command",
                      arguments=json.dumps({"argv": argv}), model_usage=usage)


def install_command_fixture(app):
    from app.services.runtime.sandbox import sandbox_command
    from app.services.runtime.sandbox.sandbox_stop import stop_and_confirm_sandbox
    from app.services.runtime.sandbox.sandbox_cleanup import cleanup_created_sandbox, cleanup_exited_sandbox
    from app.services.runtime.docker.docker_client import is_sandbox_container_absent

    created = []
    original_create = sandbox_command.create_and_confirm_sandbox
    original_lifespan = app.router.lifespan_context

    async def create(**kwargs):
        identity = await original_create(**kwargs)
        created.append((kwargs["request"], identity))
        return identity
    sandbox_command.create_and_confirm_sandbox = create

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            try:
                yield
            finally:
                # 在应用释放内存前读取真实恢复存储；仅隔离验收访问内部集合。
                records = [record for scope in application.state.command_recovery_store._scopes.values()
                           for record in scope.journal.records]
                evidence = [{"status": record.status,
                             "start_attempted": record.recovery.start_attempted if record.recovery else None,
                             "stop_confirmed": record.recovery.stop_confirmed if record.recovery else None}
                            for record in records]
                cleaned = 0
                for request, identity in created:
                    if not await is_sandbox_container_absent(container_id=identity.container_id):
                        state = await stop_and_confirm_sandbox(request=request, execution_token=identity.execution_token,
                                                              expected_container_id=identity.container_id)
                        cleanup = cleanup_created_sandbox if state.status == "created" else cleanup_exited_sandbox
                        await cleanup(request=request, execution_token=identity.execution_token,
                                      expected_container_id=identity.container_id)
                        cleaned += 1
                    assert await is_sandbox_container_absent(container_id=identity.container_id)
                report = {"created": len(created), "recovery_records": evidence,
                          "fixture_cleaned": cleaned, "all_absent": True}
                Path('/private/tmp/agent-command-browser-evidence.json').write_text(json.dumps(report))
    app.router.lifespan_context = lifespan
