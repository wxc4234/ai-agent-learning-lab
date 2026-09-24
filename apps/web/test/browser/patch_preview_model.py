"""仅控制补丁模型决策，真实授权读取/预览/聊天落库不替换。"""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AgentRun, AgentRunEvent, FileEditProposal
from app.services.runtime.agent.agent_runtime import FinalAnswer, ModelUsage, ToolAction, ToolErrorObservation


def patch_preview_decision(prompt, observations):
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    if observations:
        observation = observations[-1]
        if isinstance(observation, ToolErrorObservation):
            return FinalAnswer(content=f'预览失败：{observation.message}', model_usage=usage)
        result = json.loads(observation.result)
        assert result['status'] == 'preview_only'
        suffix = '，Diff不完整' if result['diff_truncated'] else ''
        return FinalAnswer(content=f'仅生成预览，尚未写入{suffix}', model_usage=usage)

    marker = next(name for name in ('success', 'delete', 'conflict', 'truncated') if f'[patch-preview-{name}]' in prompt)
    before = {'success': '\ufeffcount = old\n', 'delete': 'old', 'conflict': 'old\n', 'truncated': 'old' + 'X' * 20000}[marker]
    after = '' if marker == 'delete' else before.replace('old', 'new')
    def line(prefix, value):
        return prefix + value if value.endswith('\n') else prefix + value + '\n\\ No newline at end of file\n'
    patch = f'--- a/{marker}.txt\n+++ b/{marker}.txt\n'
    patch += '@@ -1 +0,0 @@\n' if marker == 'delete' else '@@ -1 +1 @@\n'
    patch += line('-', before)
    if after:
        patch += line('+', after)
    return ToolAction(
        tool_call_id='patch-preview-browser', tool_name='preview_file_patch',
        arguments=json.dumps({'relative_path': f'{marker}.txt', 'patch': patch}), model_usage=usage,
    )


def verify_patch_preview_rows(engine):
    # 浏览器输出先完成，再独立核对真实数据库；SELECT 不改变任何业务状态。
    report_path = Path('/private/tmp/agent-ui-patch-preview/output/playwright/evidence.json')
    report = json.loads(report_path.read_text())
    with Session(engine) as session:
        assert len(report) == session.scalar(select(func.count()).select_from(AgentRun)) == 4
        assert session.scalar(select(func.count()).select_from(FileEditProposal)) == 0
        for item in report:
            assert session.get(AgentRun, item['run_id']).status == 'done'
            events = session.scalars(select(AgentRunEvent).where(AgentRunEvent.run_id == item['run_id'])).all()
            assert sum(event.event_type == 'TOOL_CALL_START' for event in events) == 1
            assert sum(event.event_type == 'TOOL_CALL_RESULT' for event in events) == item['result_count']
            assert sum(event.event_type == 'TOOL_CALL_ERROR' for event in events) == item['error_count']
    report_path.with_name('database-evidence.json').write_text(json.dumps({'runs': 4, 'proposals': 0, 'verified': True}))
    print('PASS PostgreSQL: four runs, one tool call each, zero proposals.', flush=True)
