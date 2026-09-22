"""工具到真实授权保存/查询服务的闭环，模型与浏览器不参与。"""

import json

import pytest

from app.services.workspace.proposals.file_edit_proposal_service import get_task_file_edit_proposal
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY
from tests.workspace.proposals import test_file_edit_proposal_service as persistence

root = persistence.root
target = persistence.target
database = persistence.database
setup = persistence.setup


@pytest.mark.parametrize("kind", ["success", "foreign", "preview"])
def test_real_tool_persistence_and_file_boundary(setup, target, engine, kind):
    args, file, _, _ = setup
    before = file.read_bytes()
    context = ToolExecutionContext(
        user_id=target["other_id"] if kind == "foreign" else target["user_id"],
        conversation_id=target["conversation_id"],
        workspace_id=target["workspace_id"], task_id=target["task_id"],
    )
    tool = TOOL_REGISTRY["preview_file_edit" if kind == "preview" else "create_file_edit_proposal"]
    arguments = tool.validate_arguments(json.dumps({key: args[key] for key in ("relative_path", "old_text", "new_text")}))
    if kind == "foreign":
        with pytest.raises(SafeToolExecutionError) as caught:
            tool.execute(arguments, context=context)
        assert caught.value.code == "workspace_not_accessible"
        assert persistence.count(engine) == 0
    else:
        result = json.loads(tool.execute(arguments, context=context))
        if kind == "preview":
            assert result["status"] == "preview_only" and persistence.count(engine) == 0
        else:
            assert result["status"] == "pending" and persistence.count(engine) == 1
            detail = get_task_file_edit_proposal(
                user_id=context.user_id, workspace_id=context.workspace_id,
                task_id=context.task_id, proposal_id=result["proposal_id"],
            )
            assert detail.proposed_sha256 == result["proposed_sha256"]
            assert detail.baseline_sha256 == result["baseline_sha256"]
    assert file.read_bytes() == before
