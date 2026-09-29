"""通用变更组：模型只保存候选，用户统一审批；执行/恢复持有同根会话锁。"""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.engine import Engine

from app.database import SessionLocal
from app.models import FileEditProposal, TaskChangeSet
from app.repositories.workspace.file_edit_proposal_repository import lock_owned_proposal_task
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.files import change_set_files as files
from app.services.workspace.edits.workspace_edit_preview import _build_review_diff, _validate_text
from app.services.workspace.edits.workspace_unified_patch import preview_unified_patch


class ChangeOperation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['create', 'update', 'delete', 'move']
    path: str = Field(min_length=1, max_length=1024)
    content: str | None = Field(default=None, max_length=262144)
    patch: str | None = Field(default=None, max_length=524288)
    destination: str | None = Field(default=None, max_length=1024)

    @model_validator(mode='after')
    def contract(self):
        files.path_parts(self.path)
        if self.destination is not None:
            files.path_parts(self.destination)
        if ((self.kind == 'create') != (self.content is not None)
                or (self.kind == 'update') != (self.patch is not None)
                or (self.kind == 'move') != (self.destination is not None)
                or self.destination == self.path):
            raise ValueError('invalid_change_operation')
        if self.content is not None:
            _validate_text(self.content)
        return self


class ChangeSetArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operations: list[ChangeOperation] = Field(min_length=1, max_length=16)


def _binding(session, scope):
    workspace, task = lock_owned_proposal_task(session, **scope)
    if workspace.root_path is None:
        raise ValueError('change_set_project_unbound')
    return {'task_pk': task.id, 'root': workspace.root_path, 'revision': workspace.binding_revision}


def _row(session, scope, change_id):
    binding = _binding(session, scope)
    row = session.scalar(select(TaskChangeSet).where(TaskChangeSet.task_id == binding['task_pk'],
        TaskChangeSet.external_id == change_id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise WorkspaceNotAccessibleError()
    return row, binding


def _audit(row, event):
    row.audit = [*row.audit, {'event': event, 'at': datetime.now(timezone.utc).isoformat()}]


def public(row):
    return {'change_id': row.external_id, 'status': row.status, 'diff': row.diff,
            'paths': [item['path'] for item in row.entries], 'audit': row.audit}


def create_change_set(*, user_id: int, workspace_id: str, task_id: str, operations: list[dict], save_hook=None) -> dict:
    args = ChangeSetArguments.model_validate({'operations': operations})
    if sum(len((op.patch or op.content or '').encode()) for op in args.operations) > 1024 * 1024:
        raise ValueError('change_set_limit')
    scope = {'user_id': user_id, 'workspace_id': workspace_id, 'task_id': task_id}
    with SessionLocal.begin() as session:
        binding = _binding(session, scope)
    paths = [path for op in args.operations for path in (op.path, op.destination) if path is not None]
    if len(set(paths)) != len(paths):
        raise ValueError('change_set_duplicate_path')
    root_identity, observed = files.read_paths(binding['root'], paths)
    entries = []
    for op in args.operations:
        before = observed[op.path]
        if (op.kind == 'create') != (before is None):
            raise ValueError('change_set_baseline_mismatch')
        original = None if before is None else before['content']
        after = op.content if op.kind == 'create' else None
        if op.kind == 'update':
            if original is None or op.patch is None:
                raise ValueError('change_set_baseline_mismatch')
            after = preview_unified_patch(content=original, patch=op.patch, relative_path=op.path).updated_content
        entries.append({'path': op.path, 'before': original, 'after': after,
                        'identity': None if before is None else before['identity']})
        if op.kind == 'move':
            if observed[op.destination] is not None:
                raise ValueError('change_set_destination_exists')
            entries.append({'path': op.destination, 'before': None, 'after': original,
                            'identity': None, 'metadata_source': op.path})
    if sum(len((item['before'] or '').encode()) + len((item['after'] or '').encode()) for item in entries) > 2 * 1024 * 1024:
        raise ValueError('change_set_limit')
    diffs = []
    for item in entries:
        diff, truncated = _build_review_diff(item['before'] or '', item['after'] or '')
        if truncated:
            raise ValueError('change_set_diff_incomplete')
        kind = '新增' if item['before'] is None else '删除' if item['after'] is None else '修改'
        diffs.append(f"{kind} {item['path']}\n{diff}")
    diff = '\n'.join(diffs)
    if len(diff) > 65536:
        raise ValueError('change_set_diff_incomplete')
    with SessionLocal.begin() as session:
        if _binding(session, scope) != binding:
            raise ValueError('change_set_binding_changed')
        row = TaskChangeSet(external_id=uuid4().hex, task_id=binding['task_pk'], bound_root=binding['root'],
            binding_revision=binding['revision'], root_identity=root_identity, entries=entries, diff=diff, audit=[])
        _audit(row, 'created')
        session.add(row)
        session.flush()
        if save_hook is not None:
            save_hook(session, row.external_id)
        result = public(row)
    return result


def list_change_sets(**scope):
    with SessionLocal.begin() as session:
        binding = _binding(session, scope)
        rows = session.scalars(select(TaskChangeSet).where(TaskChangeSet.task_id == binding['task_pk'])
                               .order_by(TaskChangeSet.id.desc()).limit(50)).all()
        return [public(row) for row in rows]


def decide_change_set(*, change_id, action, **scope):
    if action not in {'approve', 'reject'}:
        raise ValueError('change_set_decision_invalid')
    with SessionLocal.begin() as session:
        row, binding = _row(session, scope, change_id)
        if row.status != 'pending' or (action == 'approve' and (row.bound_root != binding['root'] or row.binding_revision != binding['revision'])):
            raise ValueError('change_set_conflict')
        row.status = 'approved' if action == 'approve' else 'rejected'
        _audit(row, row.status)
        return public(row)


@contextmanager
def root_lease(root):
    """会话锁跨文件I/O持有，autocommit连接不持有数据库事务；进程退出由PG释放。"""
    key = int.from_bytes(sha256(root.encode()).digest()[:8], 'big', signed=True)
    with SessionLocal() as session:
        engine = session.get_bind()
    if not isinstance(engine, Engine):
        raise TypeError('change_set_engine_unavailable')
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        if not connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': key}):
            raise ValueError('change_set_busy')
        try:
            yield lambda: connection.execute(text('SELECT 1'))
        finally:
            connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})


def execute_change_set(*, change_id, restore=False, **scope):
    with SessionLocal.begin() as session:
        row, _ = _row(session, scope, change_id)
        root = row.bound_root
    with root_lease(root) as heartbeat:
        with SessionLocal.begin() as session:
            row, binding = _row(session, scope, change_id)
            if row.bound_root != binding['root'] or row.binding_revision != binding['revision']:
                raise ValueError('change_set_binding_changed')
            if restore and row.status == 'rolled_back':
                return public(row)
            allowed = {'applied', 'running', 'uncertain'} if restore else {'approved'}
            if row.status not in allowed:
                raise ValueError('change_set_conflict')
            busy = session.scalar(select(FileEditProposal.id).where(FileEditProposal.bound_root == root,
                FileEditProposal.application_status.in_(('running', 'uncertain'))).limit(1))
            other = session.scalar(select(TaskChangeSet.id).where(TaskChangeSet.bound_root == root,
                TaskChangeSet.id != row.id, TaskChangeSet.status.in_(('running', 'uncertain'))).limit(1))
            if busy is not None or other is not None:
                raise ValueError('change_set_busy')
            if restore and row.status == 'applied' and row.journal is not None:
                row.journal = {**row.journal, 'require_applied': True}
            entries, root_identity, journal = row.entries, row.root_identity, row.journal
            require_applied = bool(journal and journal.get('require_applied') and not journal.get('restoring'))
            row.status = 'running'
            _audit(row, 'restore_started' if restore else 'apply_started')
        def save_journal(value):
            nonlocal journal
            heartbeat()
            with SessionLocal.begin() as session:
                current, latest = _row(session, scope, change_id)
                if latest != binding or current.status != 'running':
                    raise ValueError('change_set_binding_changed')
                current.journal = value
            journal = value
        try:
            heartbeat()
            if restore:
                files.restore_files(root, root_identity, entries, journal, require_applied=require_applied,
                    begin_restore=lambda: save_journal({**journal, 'restoring': True}) if journal else None)
                status = 'rolled_back'
            else:
                journal = files.prepare(root, root_identity, entries, save_journal)
                files.apply_files(root, root_identity, entries, journal, checkpoint=lambda _: heartbeat())
                status = 'applied'
        except Exception:  # noqa: BLE001 -- 仅按持久现场尝试恢复，不重放原操作。
            status = 'uncertain'
            if not restore and journal is not None:
                try:
                    heartbeat()
                    files.restore_files(root, root_identity, entries, journal)
                    status = 'rolled_back'
                except Exception:  # noqa: BLE001 -- 外部变化与未知现场保持冻结。
                    status = 'uncertain'
            elif not restore and journal is None:
                # 没有可信现场时不声称已恢复原文；可能是基线冲突或提交确认丢失。
                status = 'uncertain'
        with SessionLocal.begin() as session:
            row, _ = _row(session, scope, change_id)
            row.status = status
            _audit(row, status)
            result = public(row)
        return result
