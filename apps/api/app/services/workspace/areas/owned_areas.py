"""用户显式创建独立项目任务；导出只生成原项目的待审批变更组。"""
import base64
from contextlib import ExitStack
from difflib import unified_diff
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from app.database import SessionLocal
from app.models import OwnedWorkArea, Workspace, Task, Conversation
from app.repositories.workspace.file_edit_proposal_repository import lock_owned_proposal_task
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.runtime.sandbox.project_snapshot import read_project_snapshot
from app.services.workspace.files.change_set_files import path_parts
from app.services.workspace.files.workspace_file_replace import _verify_directory_chain
from app.services.workspace.git.project_source import _observe_root
from app.services.workspace.proposals.change_sets import create_change_set, root_lease
from app.tools.context import ToolExecutionContext

# 本地持久数据与学习源码分开；测试注入隔离目录，不写开发工作区。
AREA_BASE = Path.home() / '.ai-agent-learning-lab' / 'workspaces'


def _context(session, scope):
    workspace, task = lock_owned_proposal_task(session, **scope)
    conversation = session.scalar(select(Conversation).where(Conversation.task_id == task.id, Conversation.user_id == scope['user_id']))
    if conversation is None or workspace.root_path is None:
        raise WorkspaceNotAccessibleError()
    return workspace, task, ToolExecutionContext(**scope, conversation_id=conversation.external_id)


def _public(session, row):
    task = session.get(Task, row.task_id)
    source_task = session.get(Task, row.source_task_id)
    if task is None or source_task is None:
        raise WorkspaceNotAccessibleError()
    return {'workspace_id': task.workspace.external_id, 'task_id': task.external_id,
            'source_workspace_id': source_task.workspace.external_id, 'source_task_id': source_task.external_id,
            'exported_change_id': row.exported_id}


def read_owned_area(**scope):
    with SessionLocal.begin() as session:
        _, task, _ = _context(session, scope)
        row = session.scalar(select(OwnedWorkArea).where(OwnedWorkArea.task_id == task.id))
        return None if row is None else _public(session, row)


def create_owned_area(**scope):
    with SessionLocal.begin() as session:
        workspace, task, context = _context(session, scope)
        binding = (workspace.root_path, workspace.binding_revision, task.id)
        name = workspace.name
    snapshot = read_project_snapshot(context)
    AREA_BASE.mkdir(parents=True, mode=0o700, exist_ok=True)
    area_name = uuid4().hex
    # 服务端选择独占路径；模型和浏览器不能指定宿主路径。
    with _observe_root(str(AREA_BASE)) as base:
        os.mkdir(area_name, 0o700, dir_fd=base.descriptor)
        area = AREA_BASE / area_name
        with _observe_root(str(area)) as opened:
            root_identity = list(opened.identity)
            for relative, content in snapshot.items():
                with ExitStack() as stack:
                    parts = path_parts(relative)
                    parent, links = opened.descriptor, []
                    for component in parts[:-1]:
                        try: os.mkdir(component, 0o700, dir_fd=parent)
                        except FileExistsError: pass
                        child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                        stack.callback(os.close, child)
                        links.append((parent, component, child))
                        parent = child
                    fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                    stack.callback(os.close, fd)
                    data = content
                    while data:
                        written = os.write(fd, data)
                        if not written: raise OSError('short write')
                        data = data[written:]
                    os.fsync(fd)
                    _verify_directory_chain(links)
            os.fsync(opened.descriptor)
    with SessionLocal.begin() as session:
        workspace, task, _ = _context(session, scope)
        if (workspace.root_path, workspace.binding_revision, task.id) != binding:
            # 失败保留私有现场，不递归删除身份或提交状态不明的数据。
            raise ValueError('owned_area_source_changed')
        child_workspace = Workspace(user_id=scope['user_id'], external_id=uuid4().hex,
            name=('隔离 · ' + name)[:100], root_path=str(area), binding_revision=1)
        session.add(child_workspace)
        session.flush()
        child_task = Task(external_id=uuid4().hex, workspace_id=child_workspace.id, title='隔离编码任务')
        session.add(child_task)
        session.flush()
        session.add(Conversation(external_id=uuid4().hex, user_id=scope['user_id'], task_id=child_task.id))
        row = OwnedWorkArea(source_task_id=task.id, task_id=child_task.id, bound_root=str(area),
            root_identity=root_identity, source_root=binding[0], source_revision=binding[1],
            baseline={path: base64.b64encode(content).decode() for path, content in snapshot.items()})
        session.add(row)
        session.flush()
        result = _public(session, row)
    return result


def _patch(path, before, after):
    pieces = []
    for line in unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                             fromfile='a/' + path, tofile='b/' + path):
        pieces.append(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n')
    return ''.join(pieces)


def export_owned_area(**scope):
    with SessionLocal.begin() as session:
        _, task, context = _context(session, scope)
        row = session.scalar(select(OwnedWorkArea).where(OwnedWorkArea.task_id == task.id))
        if row is None:
            raise WorkspaceNotAccessibleError()
        source = _public(session, row)
        root = row.bound_root
    # 与该隔离根目录中的提案应用共享锁，导出不会读到内部应用的一半。
    with root_lease(root):
        with SessionLocal.begin() as session:
            workspace, task, context = _context(session, scope)
            row = session.scalar(select(OwnedWorkArea).where(OwnedWorkArea.task_id == task.id).with_for_update())
            if row is None or workspace.root_path != row.bound_root:
                raise ValueError('owned_area_binding_changed')
            if row.exported_id is not None:
                return _public(session, row)
            baseline, expected_identity = dict(row.baseline), row.root_identity
            source_root, source_revision = row.source_root, row.source_revision
        with _observe_root(root) as opened:
            if list(opened.identity) != expected_identity:
                raise ValueError('owned_area_identity_changed')
            current = read_project_snapshot(context)
        source_scope = {'user_id': scope['user_id'], 'workspace_id': source['source_workspace_id'], 'task_id': source['source_task_id']}
        with SessionLocal.begin() as session:
            original_workspace, _, original_context = _context(session, source_scope)
            if original_workspace.root_path != source_root or original_workspace.binding_revision != source_revision:
                raise ValueError('owned_area_source_changed')
        original = read_project_snapshot(original_context)
        baseline_bytes = {path: base64.b64decode(value, validate=True) for path, value in baseline.items()}
        operations = []
        for path in sorted(set(baseline_bytes) | set(current)):
            before, after = baseline_bytes.get(path), current.get(path)
            if before == after: continue
            if original.get(path) != before:
                raise ValueError('owned_area_export_conflict')
            if before is None:
                if after is None: raise ValueError('owned_area_no_changes')
                operations.append({'kind': 'create', 'path': path, 'content': after.decode('utf-8')})
            elif after is None:
                operations.append({'kind': 'delete', 'path': path})
            else:
                operations.append({'kind': 'update', 'path': path, 'patch': _patch(path, before.decode('utf-8'), after.decode('utf-8'))})
        if not operations:
            raise ValueError('owned_area_no_changes')
        def save_export(session, change_id):
            # 与候选创建同事务确认导出回执，提交确认丢失后不会重复生成候选。
            original_workspace, _, _ = _context(session, source_scope)
            if original_workspace.root_path != source_root or original_workspace.binding_revision != source_revision:
                raise ValueError('owned_area_source_changed')
            current_workspace, current_task, _ = _context(session, scope)
            area = session.scalar(select(OwnedWorkArea).where(OwnedWorkArea.task_id == current_task.id).with_for_update())
            if area is None or current_workspace.root_path != root or area.exported_id is not None:
                raise ValueError('owned_area_export_conflict')
            area.exported_id = change_id
        create_change_set(**source_scope, operations=operations, save_hook=save_export)
        with SessionLocal.begin() as session:
            _, task, _ = _context(session, scope)
            row = session.scalar(select(OwnedWorkArea).where(OwnedWorkArea.task_id == task.id))
            if row is None: raise WorkspaceNotAccessibleError()
            return _public(session, row)


def list_owned_areas(**scope):
    with SessionLocal.begin() as session:
        _, task, _ = _context(session, scope)
        current = session.scalar(select(OwnedWorkArea).where(OwnedWorkArea.task_id == task.id))
        copies = session.scalars(select(OwnedWorkArea).where(OwnedWorkArea.source_task_id == task.id)
                                 .order_by(OwnedWorkArea.id.desc()).limit(20)).all()
        return {'current': None if current is None else _public(session, current),
                'copies': [_public(session, row) for row in copies]}
