"""从已确认应用的备份生成反向提案；恢复也必须重新审阅、批准和发放许可。"""

from hashlib import sha256

from sqlalchemy import select

from app.database import SessionLocal
from app.models import FileEditProposal, ProposalAuditEvent
from app.repositories.workspace.file_edit_proposal_repository import insert_file_edit_proposal
from app.services.workspace.edits.workspace_edit_preview import _build_review_diff, _validate_text
from app.services.workspace.files.workspace_file import read_task_text_file
from app.services.workspace.proposals.project_write_grants import (
    GrantScope, ProjectWriteGrantConflictError, _authorize,
)


def _source(session, scope: GrantScope) -> tuple[dict, str]:
    source = _authorize(session, scope)
    row = session.get(FileEditProposal, source['proposal_pk'])
    if (row is None or row.baseline_content is None or source['application_status'] != 'applied'
            or source['current_root'] != source['bound_root']):
        raise ProjectWriteGrantConflictError()
    if sha256(row.baseline_content.encode()).hexdigest() != source['baseline_sha256']:
        raise ProjectWriteGrantConflictError()
    return source, row.baseline_content


def _existing(session, proposal_pk: int) -> str | None:
    # 原提案行锁串行化同一恢复请求；短事件保存公开ID，既不复制正文也不暴露执行令牌。
    event = session.scalar(select(ProposalAuditEvent.event).where(
        ProposalAuditEvent.proposal_id == proposal_pk,
        ProposalAuditEvent.event.like('restore:%'),
    ))
    return event.removeprefix('restore:') if event else None


def create_restore_proposal(*, user_id: int, workspace_id: str, task_id: str, proposal_id: str) -> str:
    """网络确认丢失后重复请求返回同一反向提案，不重写文件或再建一份提案。"""
    typed: GrantScope = {'user_id': user_id, 'workspace_id': workspace_id,
                         'task_id': task_id, 'proposal_id': proposal_id}
    with SessionLocal.begin() as session:
        source, backup = _source(session, typed)
        existing = _existing(session, source['proposal_pk'])
        if existing:
            return existing
    # 文件读取不占用事务。未知或仍运行的原请求不参与恢复，避免与迟到写入竞争。
    current = read_task_text_file(user_id=typed['user_id'], workspace_id=typed['workspace_id'],
                                  task_id=typed['task_id'], relative_path=source['relative_path'])
    if sha256(current.content.encode()).hexdigest() != source['proposed_sha256']:
        raise ProjectWriteGrantConflictError()
    _validate_text(backup)
    diff, truncated = _build_review_diff(current.content, backup)
    if truncated:
        raise ProjectWriteGrantConflictError()
    with SessionLocal.begin() as session:
        latest, latest_backup = _source(session, typed)
        existing = _existing(session, latest['proposal_pk'])
        if existing:
            return existing
        if latest != source or latest_backup != backup:
            raise ProjectWriteGrantConflictError()
        reverse = insert_file_edit_proposal(
            session, task_id=source['task_pk'], bound_root=source['bound_root'],
            relative_path=source['relative_path'], baseline_sha256=source['proposed_sha256'],
            proposed_content=backup, proposed_sha256=source['baseline_sha256'],
            diff=diff, diff_truncated=False,
        )
        session.add_all([
            ProposalAuditEvent(proposal_id=reverse.id, actor_id=typed['user_id'], event='created'),
            ProposalAuditEvent(proposal_id=source['proposal_pk'], actor_id=typed['user_id'],
                               event='restore:' + reverse.external_id),
        ])
        result = reverse.external_id
    # 返回只表示反向提案持久化；不复用原许可，不绕过审批。
    return result


def read_proposal_audit(*, user_id: int, workspace_id: str, task_id: str, proposal_id: str) -> list[dict]:
    scope: GrantScope = {'user_id': user_id, 'workspace_id': workspace_id, 'task_id': task_id, 'proposal_id': proposal_id}
    with SessionLocal.begin() as session:
        source = _authorize(session, scope)
        rows = session.scalars(select(ProposalAuditEvent).where(
            ProposalAuditEvent.proposal_id == source['proposal_pk'],
        ).order_by(ProposalAuditEvent.id).limit(100)).all()
        return [{'event': 'restore_requested' if row.event.startswith('restore:') else row.event,
                     'created_at': row.created_at.isoformat(),
                     'restore_proposal_id': row.event[8:] if row.event.startswith('restore:') else None}
                for row in rows]
