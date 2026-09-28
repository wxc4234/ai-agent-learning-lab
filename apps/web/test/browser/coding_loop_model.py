"""联合冒烟只替换模型决策；提案、Git和Docker均走生产服务。"""

from contextlib import asynccontextmanager
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentRun, FileEditProposal
from app.services.runtime.docker.docker_client import is_sandbox_container_absent
from app.services.runtime.agent.agent_runtime import FinalAnswer, ModelUsage, ToolAction
from patch_application_model import install_patch_application_fixture, patch_application_decision

OUTPUT = Path(__file__).resolve().parents[2] / 'output/playwright/coding-loop'


def install_coding_loop_fixture(app):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ('cleanup.json', 'evidence.json', 'resources.json'):
        (OUTPUT / name).unlink(missing_ok=True)
    install_patch_application_fixture(app, output=OUTPUT, markers=('approved', 'rejected', 'stale'))
    prepared = app.router.lifespan_context

    @asynccontextmanager
    async def audited(application):
        async with prepared(application):
            yield
            # 仅核对本轮应用保留的实际回执，不替换执行器或通过全局扫描推断清理。
            store = application.state.verification_recovery_store
            records = [record for scope in store._scopes.values() for record in scope.journal.records]
            assert len(records) == 3
            for record in records:
                assert record.status == 'completed' and record.sample_cleaned
                assert record.container_id is not None
                assert await is_sandbox_container_absent(container_id=record.container_id)
            (OUTPUT / 'resources.json').write_text(json.dumps({
                'completed_verifications': len(records), 'containers_absent': True, 'snapshots_cleaned': True,
            }))
    app.router.lifespan_context = audited


def coding_loop_decision(prompt, observations):
    # 历史消息也含提案标记；以本轮最后一个标记决定动作。
    if prompt.rfind('[coding-loop-check]') < prompt.rfind('[coding-loop-propose]'):
        return patch_application_decision(prompt, observations)
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    if not observations:
        return ToolAction(tool_call_id='diff', tool_name='read_task_sample_diff', arguments='{}', model_usage=usage)
    if len(observations) == 1:
        assert json.loads(observations[0].result)['source'] == 'task_application_sample'
        return ToolAction(tool_call_id='verify', tool_name='verify_task_sample',
            arguments=json.dumps({'plan_id': 'sample_unittest_v1'}), model_usage=usage)
    assert json.loads(observations[-1].result)['outcome'] in ('passed', 'failed')
    return FinalAnswer(content='差异与固定验证已记录。', model_usage=usage)


def verify_coding_loop_rows(engine):
    report = json.loads((OUTPUT / 'evidence.json').read_text())
    fixtures = json.loads((OUTPUT / 'fixtures.json').read_text())
    with Session(engine) as session:
        rows = list(session.scalars(select(FileEditProposal)))
        runs = list(session.scalars(select(AgentRun)))
        assert len(rows) == len(report) == 3
        assert len(runs) == 6 and all(run.status == 'done' for run in runs)
        for item, fixture in zip(report, fixtures, strict=True):
            mode = fixture['marker']
            row = next(row for row in rows if row.external_id == item['proposal_id'])
            assert row.status == ('rejected' if mode == 'rejected' else 'approved')
            assert row.application_status == {'approved': 'applied', 'rejected': 'idle', 'stale': 'not_applied'}[mode]
            assert (Path(fixture['root']) / 'example.txt').read_bytes() == {
                'approved': b'new\n', 'rejected': b'old\n', 'stale': b'external\n',
            }[mode]
    print('PASS real database/file: 3 proposals, 6 completed runs, exact decisions and application states', flush=True)
