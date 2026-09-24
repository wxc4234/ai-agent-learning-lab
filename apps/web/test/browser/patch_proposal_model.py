"""补丁提案浏览器的受控模型及真实隔离数据库核对。"""

import json
from difflib import unified_diff
from hashlib import sha256
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentRun, AgentRunEvent, FileEditProposal, Task
from app.services.runtime.agent.agent_runtime import FinalAnswer, ModelUsage, ToolAction, ToolErrorObservation


def patch_proposal_decision(prompt, observations):
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    marker = next(name for name in ('success', 'delete', 'conflict', 'truncated', 'unconfirmed') if f'[patch-proposal-{name}]' in prompt)
    if not observations:
        before = '\ufeffold\n' if marker == 'success' else 'old\n'
        after = '' if marker == 'delete' else 'X' * 20000 + '\n' if marker == 'truncated' else before.replace('old', 'new')
        patch = ''.join(unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                     fromfile=f'a/{marker}.txt', tofile=f'b/{marker}.txt'))
        return ToolAction(tool_call_id='patch-proposal-browser', tool_name='create_file_patch_proposal',
                          arguments=json.dumps({'relative_path': f'{marker}.txt', 'patch': patch}), model_usage=usage)
    if isinstance(observations[-1], ToolErrorObservation):
        return FinalAnswer(content='提案调用失败，请核对结果，勿自动重试', model_usage=usage)
    assert json.loads(observations[-1].result)['status'] == 'pending'
    return FinalAnswer(content='提案已保存，等待审批，文件尚未修改', model_usage=usage)


def verify_patch_proposal_rows(engine):
    report = json.loads(Path('/private/tmp/agent-ui-patch-proposal/output/playwright/evidence.json').read_text())
    with Session(engine) as session:
        rows = list(session.scalars(select(FileEditProposal)))
        assert len(rows) == 4 and len(report) == 5
        for item in report:
            task = session.scalar(select(Task).where(Task.external_id == item['task_id']))
            proposals = [row for row in rows if row.task_id == task.id]
            run = session.get(AgentRun, item['run_id'])
            assert run.status == 'done'
            events = list(session.scalars(select(AgentRunEvent).where(AgentRunEvent.run_id == run.id)))
            assert sum(e.event_type == 'TOOL_CALL_START' for e in events) == 1
            failed = item['marker'] in ('conflict', 'unconfirmed')
            assert sum(e.event_type == 'TOOL_CALL_ERROR' for e in events) == int(failed)
            assert sum(e.event_type == 'TOOL_CALL_RESULT' for e in events) == int(not failed)
            if item['marker'] == 'conflict':
                assert not proposals
                continue
            assert len(proposals) == 1
            row = proposals[0]
            assert row.status == 'pending' and row.proposed_content == item['expected_content']
            assert row.proposed_sha256 == sha256(item['expected_content'].encode()).hexdigest()
            original = '\ufeffold\n' if item['marker'] == 'success' else 'old\n'
            assert row.baseline_sha256 == sha256(original.encode()).hexdigest()
            if not failed:
                assert row.external_id == item['result']['proposal_id']
            assert row.diff_truncated == (item['marker'] == 'truncated')
    print('PASS PostgreSQL: 5 runs, 4 pending proposals including unconfirmed, conflict absent, exact candidates/hashes, no duplicate save.', flush=True)
