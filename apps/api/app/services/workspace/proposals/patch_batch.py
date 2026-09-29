"""多文件补丁一次预览、同事务保存；审批与应用仍明确到每份文件提案。"""
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from app.database import SessionLocal
from app.models import ProposalAuditEvent
from app.repositories.workspace.project_git_repository import read_owned_git_binding
from app.repositories.workspace.file_edit_proposal_repository import lock_owned_proposal_task, insert_file_edit_proposal
from app.services.workspace.edits.workspace_patch_preview import preview_task_file_patch
from app.services.workspace.proposals.file_edit_proposal_service import ProposalBindingChangedError
from app.tools.context import ToolExecutionContext


class FilePatch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    relative_path: str = Field(min_length=1, max_length=1024)
    patch: str = Field(min_length=1, max_length=524288)


class PatchBatchArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    patches: list[FilePatch] = Field(min_length=1, max_length=16)


def create_patch_batch(*, context: ToolExecutionContext, patches: list[dict]) -> list[dict]:
    """所有候选验证通过才进入唯一写事务，不留下半批pending记录。"""
    args = PatchBatchArguments.model_validate({'patches': patches})
    if sum(len(item.patch.encode()) for item in args.patches) > 1024 * 1024:
        raise ValueError('patch_batch_limit')
    scope = {'user_id': context.user_id, 'workspace_id': context.workspace_id, 'task_id': context.task_id}
    with SessionLocal() as session:
        source = dict(read_owned_git_binding(session, **scope))
    previews = [preview_task_file_patch(**scope, **item.model_dump()) for item in args.patches]
    if len({item.relative_path for item in previews}) != len(previews):
        raise ValueError('patch_batch_duplicate_target')
    # 规范路径后再去重；两个写法不得生成针对同一文件的不同候选。
    results = []
    with SessionLocal.begin() as session:
        _, task = lock_owned_proposal_task(session, **scope)
        if dict(read_owned_git_binding(session, **scope)) != source:
            raise ProposalBindingChangedError()
        for item in previews:
            row = insert_file_edit_proposal(session, task_id=task.id, bound_root=source['root_path'],
                relative_path=item.relative_path, baseline_sha256=item.baseline_sha256,
                proposed_content=item.preview.updated_content,
                proposed_sha256=sha256(item.preview.updated_content.encode()).hexdigest(),
                diff=item.preview.diff, diff_truncated=item.preview.diff_truncated)
            session.add(ProposalAuditEvent(proposal_id=row.id, actor_id=context.user_id, event='created'))
            results.append({'proposal_id': row.external_id, 'relative_path': row.relative_path,
                            'status': row.status, 'diff_truncated': row.diff_truncated})
    return results
