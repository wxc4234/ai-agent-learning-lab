"""普通项目确定性编码冒烟：真实PG、文件、审批、Docker，无模型费用。

从apps/api：../../.venv/bin/python -m pytest ../../scripts/verify_project_coding_loop.py -q -s
"""
import asyncio
import json
from pathlib import Path
from time import monotonic

import pytest
from sqlalchemy.orm import sessionmaker

from app.services.workspace.proposals import patch_batch, file_edit_proposal_decision
from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.sandbox.project_command import run_project_command
from app.services.runtime.sandbox.task_sample_command_journal import TaskSampleCommandJournal
from tests.workspace.proposals.test_project_snapshot import context
from tests.workspace.proposals.test_project_write_execution import (
    grants, ready, setup, saved, root, target, database, execution_database,
)

pytest_plugins = ['tests.conftest']
__all__ = ['context', 'database', 'execution_database', 'grants', 'ready', 'root', 'saved', 'setup', 'target']
OUTPUT = Path(__file__).resolve().parents[1] / 'apps/web/output/playwright/week5-smoke'


@pytest.mark.parametrize('scenario', ['single_file', 'cross_file', 'rejected', 'failed_test', 'dangerous_command'])
def test_project_coding_loop(scenario, context, root, grants, engine, setup, monkeypatch):
    started = monotonic()
    factory = sessionmaker(bind=engine, class_=setup[3])
    monkeypatch.setattr(patch_batch, 'SessionLocal', factory)
    monkeypatch.setattr(file_edit_proposal_decision, 'SessionLocal', factory)
    (root / 'a.py').write_text('value = 0\n')
    (root / 'b.py').write_text('value = 0\n')
    traces = []
    def command(code):
        journal = TaskSampleCommandJournal(user_id=context.user_id, conversation_id=context.conversation_id)
        result = asyncio.run(run_project_command(request=CommandRequest(
            argv=['/usr/local/bin/python', '-c', code]), context=context, journal=journal))
        traces.append({'tool': 'run_command', 'status': result.command.status,
                       'exit_code': result.command.exit_code, 'duration_ms': result.command.duration_ms})
        assert result.sample_cleaned
        return result.command.exit_code
    if scenario == 'dangerous_command':
        assert command("from pathlib import Path; Path('/workspace/example.txt').write_text('forbidden')") != 0
        assert (root / 'a.py').read_text() == 'value = 0\n'
        diff = []
    else:
        count = 2 if scenario == 'cross_file' else 1
        check = 'import a,b; assert a.value + b.value == ' + str(count)
        assert command(check) != 0
        names = ['a.py', 'b.py'][:count]
        diff = [{'relative_path': name, 'patch': f'--- a/{name}\n+++ b/{name}\n@@ -1 +1 @@\n-value = 0\n+value = 1\n'} for name in names]
        proposals = patch_batch.create_patch_batch(context=context, patches=diff)
        traces.append({'tool': 'create_patch_proposals', 'count': len(proposals), 'status': 'pending'})
        for item in proposals:
            scope = {'user_id': context.user_id, 'workspace_id': context.workspace_id,
                     'task_id': context.task_id, 'proposal_id': item['proposal_id']}
            decision = 'rejected' if scenario == 'rejected' else 'approved'
            file_edit_proposal_decision.decide_task_file_edit_proposal(**scope, decision=decision)
            traces.append({'tool': 'user_decision', 'status': decision})
            if decision == 'approved':
                grant = grants.issue(**scope)
                result = grants.apply(**scope, grant_id=grant.grant_id, revision=1)
                assert result.application_status == 'applied'
                traces.append({'tool': 'apply', 'status': result.application_status})
        code = check if scenario != 'failed_test' else 'raise SystemExit(4)'
        expected = 0 if scenario in ('single_file', 'cross_file') else 1 if scenario == 'rejected' else 4
        assert command(code) == expected
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / f'{scenario}.json').write_text(json.dumps({
        'scenario': scenario, 'status': 'passed', 'steps': traces, 'diff': diff,
        'duration_ms': round((monotonic() - started) * 1000),
        'model_usage': None, 'model_cost': None, 'model_called': False,
    }, ensure_ascii=False, indent=2))


@pytest.mark.parametrize('scenario', ['find', 'single_file', 'cross_file', 'rejected', 'failed_test', 'dangerous_command'])
def test_agent_runtime_coding_loop(scenario, context, root, grants, engine, setup, monkeypatch):
    """受控决策替身驱动真实Runtime/Registry/Observation；用户审批在两轮运行之间。"""
    from dataclasses import asdict
    from app.services.runtime.agent.agent_runtime import (
        AgentLoopCompleted, FinalAnswer, ToolAction, ToolObservation, stream_agent_loop,
    )
    from app.services.runtime.execution.execution_threads import ExecutionThreads
    from app.tools.registry import tools_for_execution

    started = monotonic()
    factory = sessionmaker(bind=engine, class_=setup[3])
    monkeypatch.setattr(patch_batch, 'SessionLocal', factory)
    monkeypatch.setattr(file_edit_proposal_decision, 'SessionLocal', factory)
    (root / 'a.py').write_text('value = 0\n')
    (root / 'b.py').write_text('value = 0\n')
    count = 2 if scenario == 'cross_file' else 1
    diff = [{'relative_path': name, 'patch': f'--- a/{name}\n+++ b/{name}\n@@ -1 +1 @@\n-value = 0\n+value = 1\n'}
            for name in ['a.py', 'b.py'][:count]]
    check = 'import a,b; assert a.value + b.value == ' + str(count)
    traces, events, outcomes = [], [], []

    async def execute_command(**arguments):
        journal = TaskSampleCommandJournal(user_id=context.user_id, conversation_id=context.conversation_id)
        result = await run_project_command(request=CommandRequest(**arguments), context=context, journal=journal)
        assert result.sample_cleaned
        outcomes.append(result.command.exit_code)
        return result.command.model_dump_json()

    async def phase(actions):
        async def decide(observations):
            # 每一轮确实消费上轮回灌，失败工具不被决策替身忽略成成功。
            assert all(isinstance(item, ToolObservation) for item in observations), observations
            if len(observations) < len(actions):
                name, arguments = actions[len(observations)]
                return ToolAction(str(len(observations)), name, json.dumps(arguments))
            return FinalAnswer('受控任务阶段完成')
        tracker = ExecutionThreads()
        terminal = None
        try:
            async for event in stream_agent_loop(decide, max_steps=len(actions) + 1, tool_context=context,
                    execution_threads=tracker, tool_definitions=tools_for_execution(
                        context=context, command_executor=execute_command, project_snapshot=True)):
                events.append(type(event).__name__)
                if isinstance(event, AgentLoopCompleted):
                    terminal = event.result
        finally:
            await tracker.wait_closed()
        assert terminal is not None and terminal.status == 'completed'
        traces.extend(asdict(item) for item in terminal.observations)
        return terminal

    def command(code):
        return ('run_command', {'argv': ['/usr/local/bin/python', '-c', code]})

    if scenario == 'dangerous_command':
        asyncio.run(phase([command("from pathlib import Path; Path('/workspace/example.txt').write_text('forbidden')")]))
        assert outcomes == [1] and (root / 'a.py').read_text() == 'value = 0\n'
        diff = []
    else:
        actions = [('find_files', {'query': '.py'}), ('read_text_file', {'relative_path': 'a.py'})]
        if scenario != 'find':
            actions += [command(check), ('create_patch_proposals', {'patches': diff})]
        first = asyncio.run(phase(actions))
        assert 'a.py' in first.observations[0].result
        if scenario == 'find':
            diff = []
        else:
            assert outcomes == [1]
            proposals = json.loads(first.observations[-1].result)['proposals']
            for proposal in proposals:
                scope = {'user_id': context.user_id, 'workspace_id': context.workspace_id,
                         'task_id': context.task_id, 'proposal_id': proposal['proposal_id']}
                decision = 'rejected' if scenario == 'rejected' else 'approved'
                file_edit_proposal_decision.decide_task_file_edit_proposal(**scope, decision=decision)
                traces.append({'user_decision': decision, 'proposal_id': proposal['proposal_id']})
                if decision == 'approved':
                    grant = grants.issue(**scope)
                    receipt = grants.apply(**scope, grant_id=grant.grant_id, revision=1)
                    assert receipt.application_status == 'applied'
            asyncio.run(phase([command('raise SystemExit(4)' if scenario == 'failed_test' else check)]))
            assert outcomes[-1] == (4 if scenario == 'failed_test' else 1 if scenario == 'rejected' else 0)
    assert events.count('AgentLoopCompleted') == (2 if scenario not in {'find', 'dangerous_command'} else 1)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / f'agent-{scenario}.json').write_text(json.dumps({
        'scenario': scenario, 'status': 'passed', 'runtime': 'stream_agent_loop', 'events': events,
        'steps': traces, 'exit_codes': outcomes, 'diff': diff,
        'duration_ms': round((monotonic() - started) * 1000),
        'model_usage': None, 'model_cost': None, 'model_called': False,
        'decision_source': 'deterministic_test_double',
    }, ensure_ascii=False, indent=2))
