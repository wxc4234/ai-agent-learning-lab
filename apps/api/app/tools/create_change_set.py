"""模型生成通用变更组，不获得审批或文件执行能力。"""
import json
from app.services.workspace.proposals.change_sets import ChangeSetArguments, create_change_set
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError


def create_change_set_tool(*, context: ToolExecutionContext, operations: list[dict]) -> str:
    try:
        result = create_change_set(user_id=context.user_id, workspace_id=context.workspace_id,
                                   task_id=context.task_id, operations=operations)
        return json.dumps({key: result[key] for key in ('change_id', 'status', 'paths')}, ensure_ascii=False)
    except Exception:  # noqa: BLE001 -- 不把原文或路径异常作为模型观察，也不自动重放提交。
        raise SafeToolExecutionError('change_set_creation_unconfirmed') from None

__all__ = ['ChangeSetArguments', 'create_change_set_tool']
