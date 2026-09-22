"""提案浏览器受控模型及隔离数据库验收，不替换工具或文件服务。"""

import json
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import FileEditProposal, Task
from app.services.runtime.agent.agent_runtime import FinalAnswer, ModelUsage, ToolAction, ToolErrorObservation
from app.services.workspace.proposals.file_edit_proposal_service import get_task_file_edit_proposal


def proposal_decision(prompt, observations):
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    marker = next(name for name in ("success", "delete", "ambiguous", "truncated", "preview") if f"[proposal-{name}]" in prompt)
    if not observations:
        return ToolAction(
            tool_call_id="proposal-browser",
            tool_name="preview_file_edit" if marker == "preview" else "create_file_edit_proposal",
            arguments=json.dumps({"relative_path": f"{marker}.txt", "old_text": "old", "new_text": "" if marker == "delete" else "new"}),
            model_usage=usage,
        )
    observation = observations[-1]
    if isinstance(observation, ToolErrorObservation):
        return FinalAnswer(content=f"提案失败：{observation.message}", model_usage=usage)
    result = json.loads(observation.result)
    expected = "preview_only" if marker == "preview" else "pending"
    assert result["status"] == expected
    text = "仅生成预览，尚未写入" if marker == "preview" else "提案已保存，等待审批，文件尚未修改"
    return FinalAnswer(content=text, model_usage=usage)


def verify_proposal_rows(engine):
    # 浏览器证据只在隔离启动器中读取，不为生产应用增加测试HTTP接口。
    report = json.loads(Path("/private/tmp/agent-ui-proposal/output/playwright/evidence.json").read_text())
    # 启动器进程的默认Session仍可能指向开发库，必须显式绑定隔离engine。
    with patch("app.services.workspace.proposals.file_edit_proposal_service.SessionLocal", sessionmaker(bind=engine)), Session(engine) as session:
        rows = list(session.scalars(select(FileEditProposal)))
        assert len(rows) == 3, "刷新不得新增提案；失败及纯预览不保存"
        for item in report:
            task = session.scalar(select(Task).where(Task.external_id == item["task_id"]))
            proposals = [row for row in rows if row.task_id == task.id]
            if item["marker"] in ("ambiguous", "preview"):
                assert not proposals
                continue
            assert len(proposals) == 1
            row = proposals[0]
            assert row.external_id == item["result"]["proposal_id"]
            assert row.status == "pending"
            assert row.proposed_content == item["expected_content"]
            assert row.proposed_sha256 == sha256(item["expected_content"].encode()).hexdigest()
            assert row.baseline_sha256 == item["result"]["baseline_sha256"]
            assert row.diff_truncated == (item["marker"] == "truncated")
            detail = get_task_file_edit_proposal(
                user_id=task.workspace.user_id, workspace_id=task.workspace.external_id,
                task_id=task.external_id, proposal_id=row.external_id,
            )
            assert detail.diff == row.diff and detail.proposed_sha256 == row.proposed_sha256
    print("PASS PostgreSQL: exactly 3 pending proposals, exact content/hashes, authorized query; preview/failure absent, reload no duplicates.", flush=True)
