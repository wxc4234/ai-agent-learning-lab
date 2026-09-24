"""任务改动列表只读快照，不读文件或将审批推断为已应用。"""

from app.database import SessionLocal
from app.repositories.workspace.file_edit_proposal_repository import list_owned_file_edit_proposals
from app.services.tasks.task_workspace import owned_task


def list_task_file_edit_proposals(*, user_id, workspace_id, task_id, before=None):
    # 独立只读事务，空列表也必须先验证Task归属；出Session后不携带ORM对象。
    with SessionLocal() as session:
        owned_task(session, user_id, workspace_id, task_id)
        rows = list_owned_file_edit_proposals(
            session, user_id=user_id, workspace_id=workspace_id, task_id=task_id, before=before,
        )
        return {
            'workspace_id': workspace_id, 'task_id': task_id,
            'items': [{
                'proposal_id': row.external_id, 'relative_path': row.relative_path,
                'status': row.status, 'application_status': row.application_status,
                'diff_truncated': row.diff_truncated,
            } for row in rows[:50]],
            'next_cursor': str(rows[49].id) if len(rows) > 50 else None,
        }
