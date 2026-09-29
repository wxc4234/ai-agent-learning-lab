"""模型可保存多文件补丁提案，不能批准、取得许可或执行副作用。"""
import json
from app.services.workspace.proposals.patch_batch import PatchBatchArguments, create_patch_batch
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError


def create_patch_proposals(*, context: ToolExecutionContext, patches: list[dict]) -> str:
    try:
        return json.dumps({'proposals': create_patch_batch(context=context, patches=patches)}, ensure_ascii=False)
    except Exception:  # noqa: BLE001 -- 提交确认可能丢失，禁止自动重试及正文回显。
        raise SafeToolExecutionError('patch_batch_save_unconfirmed') from None

__all__ = ['PatchBatchArguments', 'create_patch_proposals']
