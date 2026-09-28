"""真实隔离PostgreSQL及临时文件核对只读目标快照，不执行文件应用。"""

import os
from contextlib import contextmanager
from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Conversation, FileEditProposal, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.files import project_file_observation as observation
from app.services.workspace.proposals import project_write_snapshot as service
from tests.workspace.proposals.test_file_edit_proposal_preflight import ready, setup, saved, root, target, database
from tests.assertions import require_value

__all__ = ['database', 'ready', 'root', 'saved', 'setup', 'target']


@pytest.fixture
def reader(ready, setup, engine, monkeypatch):
    monkeypatch.setattr(service, 'SessionLocal', sessionmaker(bind=engine, class_=setup[3]))
    return service.ProjectWriteSnapshotReader()


def test_real_snapshot_private_readonly_and_repeated_instance_identity(reader, ready, database, monkeypatch):
    identity, file, sessions = ready
    statements = database[1]
    statements.clear()
    verify = observation._verify_original
    def checked(*args):
        assert all(s.closed and not s.in_transaction() for s in sessions)
        return verify(*args)
    monkeypatch.setattr(observation, '_verify_original', checked)
    monkeypatch.setattr(sessions[0].__class__, 'commit', lambda self: pytest.fail('no commit'))
    before = file.read_bytes(), file.stat()
    result = reader.read(**identity)
    assert result == reader.read(**identity)
    assert result.runtime_id != service.ProjectWriteSnapshotReader().read(**identity).runtime_id
    assert result.file_identity.inode == before[1].st_ino
    assert result.root_identity.inode == file.parent.parent.stat().st_ino
    assert result.baseline_sha256 == sha256(before[0]).hexdigest()
    assert result.proposed_sha256 == sha256(before[0].replace(b'old', b'new')).hexdigest()
    assert result.binding_revision == 1
    assert result.relative_path == 'src/中文 file.txt'
    assert str(file.parent.parent) not in result.model_dump_json()
    assert all(sql.lstrip().lower().startswith('select') and 'for update' not in sql.lower() for sql in statements)
    assert (file.read_bytes(), file.stat().st_mtime_ns) == (before[0], before[1].st_mtime_ns)


@pytest.mark.parametrize('field', ['user_id', 'workspace_id', 'task_id', 'proposal_id'])
def test_unauthorized_never_opens_file(reader, ready, field, monkeypatch):
    args = dict(ready[0])
    args[field] = 99999 if field == 'user_id' else 'f' * 32
    monkeypatch.setattr(service, 'observe_project_file', lambda **kw: pytest.fail('authorize first'))
    with pytest.raises(WorkspaceNotAccessibleError):
        reader.read(**args)


@pytest.mark.parametrize('change', ['pending', 'rejected', 'running', 'not_applied', 'uncertain', 'applied', 'truncated', 'candidate', 'binding'])
def test_invalid_proposal_rejected_before_file_io(reader, ready, engine, monkeypatch, change):
    with Session(engine) as session, session.begin():
        proposal = require_value(session.scalar(select(FileEditProposal)))
        if change in ('pending', 'rejected'):
            proposal.status = change
        elif change == 'truncated':
            proposal.status = 'pending'
            proposal.diff_truncated = True
        elif change == 'candidate':
            proposal.proposed_content = 'corrupt'
        elif change == 'binding':
            require_value(session.scalar(select(Workspace))).root_path = None
        else:
            proposal.application_status = change
            proposal.application_token = 'a' * 32
    monkeypatch.setattr(service, 'observe_project_file', lambda **kw: pytest.fail('no file read'))
    with pytest.raises(service.ProjectWriteSnapshotError):
        reader.read(**ready[0])


@pytest.mark.parametrize('change', ['revision', 'owner', 'conversation', 'candidate'])
def test_database_recheck_refuses_mid_observation_change(reader, ready, engine, monkeypatch, change):
    observe = service.observe_project_file
    @contextmanager
    def changed(**kwargs):
        with observe(**kwargs) as result:
            with Session(engine) as session, session.begin():
                if change == 'revision':
                    workspace = require_value(session.scalar(select(Workspace)))
                    workspace.binding_revision += 2  # 同路径A→B→A留下的新版本。
                elif change == 'owner':
                    require_value(session.scalar(select(Workspace))).user_id += 1
                elif change == 'conversation':
                    session.delete(require_value(session.scalar(select(Conversation))))
                else:
                    require_value(session.scalar(select(FileEditProposal))).proposed_content = 'changed'
            yield result
    monkeypatch.setattr(service, 'observe_project_file', changed)
    with pytest.raises((service.ProjectWriteSnapshotError, WorkspaceNotAccessibleError)):
        reader.read(**ready[0])


@pytest.mark.parametrize('change', ['content', 'same_bytes_new_inode', 'root', 'symlink', 'mode'])
def test_final_descriptor_recheck_refuses_file_changes(reader, ready, monkeypatch, change):
    file = ready[1]
    query = service.read_owned_proposal_application_source
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        result = query(*args, **kwargs)
        calls += 1
        if calls == 2:
            if change == 'content':
                file.write_bytes(b'external')
            elif change == 'same_bytes_new_inode':
                replacement = file.with_name('replacement')
                replacement.write_bytes(file.read_bytes())
                replacement.replace(file)
            elif change == 'root':
                parent = file.parent.parent
                parent.rename(parent.with_name('moved-root'))
                parent.mkdir()
            elif change == 'symlink':
                file.unlink()
                file.symlink_to('missing')
            else:
                file.chmod(0o400)
        return result
    monkeypatch.setattr(service, 'read_owned_proposal_application_source', changed)
    with pytest.raises(service.ProjectWriteSnapshotError):
        reader.read(**ready[0])


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'large', 'missing', 'directory', 'unsupported'])
def test_unsupported_source_is_safe_failure(reader, ready, monkeypatch, kind):
    file = ready[1]
    if kind == 'symlink':
        other = file.with_name('other')
        file.rename(other)
        file.symlink_to(other)
    elif kind == 'hardlink':
        os.link(file, file.with_name('alias'))
    elif kind == 'large':
        file.write_bytes(b'x' * (256 * 1024 + 1))
    elif kind in ('missing', 'directory'):
        file.unlink()
        if kind == 'directory':
            file.mkdir()
    else:
        def fail():
            raise ValueError('PRIVATE native failure')
        monkeypatch.setattr(observation, '_require_supported_platform', fail)
    with pytest.raises(service.ProjectWriteSnapshotError) as caught:
        reader.read(**ready[0])
    assert str(caught.value) == 'project_write_snapshot_unavailable'


@pytest.mark.parametrize('failure', ['database', 'file', 'cancel'])
def test_descriptors_and_sessions_close_on_late_failure(reader, ready, monkeypatch, failure):
    opened, closed = [], []
    original_open, original_close = os.open, os.close
    # 包装os.open后平台函数集合不再含该对象；能力检查用未替换前的真实结果。
    observation._require_supported_platform()
    monkeypatch.setattr(observation, '_require_supported_platform', lambda: None)
    def tracked_open(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        opened.append(fd)
        return fd
    def tracked_close(fd):
        closed.append(fd)
        return original_close(fd)
    monkeypatch.setattr(os, 'open', tracked_open)
    monkeypatch.setattr(os, 'close', tracked_close)
    query = service.read_owned_proposal_application_source
    calls = 0
    def broken(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            if failure == 'cancel':
                raise KeyboardInterrupt()
            if failure == 'database':
                raise RuntimeError('PRIVATE database error')
            ready[1].write_bytes(b'changed')
        return query(*args, **kwargs)
    monkeypatch.setattr(service, 'read_owned_proposal_application_source', broken)
    with pytest.raises(KeyboardInterrupt if failure == 'cancel' else service.ProjectWriteSnapshotError):
        reader.read(**ready[0])
    assert opened and sorted(opened) == sorted(closed)
    assert all(s.closed and not s.in_transaction() for s in ready[2])


def test_inherited_reader_rejected_before_database(reader, ready, monkeypatch):
    reader._pid = -1
    monkeypatch.setattr(service, 'SessionLocal', lambda: pytest.fail('fork must reject first'))
    with pytest.raises(service.ProjectWriteSnapshotError):
        reader.read(**ready[0])


@pytest.mark.parametrize('path', ['../outside', '/outside', './src/file', 'a//b'])
def test_invalid_saved_path_rejected_before_open(reader, ready, engine, monkeypatch, path):
    with Session(engine) as session, session.begin():
        require_value(session.scalar(select(FileEditProposal))).relative_path = path
    observation._require_supported_platform()
    monkeypatch.setattr(observation, '_require_supported_platform', lambda: None)
    monkeypatch.setattr(os, 'open', lambda *args, **kw: pytest.fail('invalid path must not open'))
    with pytest.raises(service.ProjectWriteSnapshotError):
        reader.read(**ready[0])


def test_observer_rejects_overlong_path_before_open(monkeypatch):
    # 数据库列自身已限制长度；直接测试内部观察器也有独立预算。
    observation._require_supported_platform()
    monkeypatch.setattr(observation, '_require_supported_platform', lambda: None)
    monkeypatch.setattr(os, 'open', lambda *args, **kw: pytest.fail('must not open'))
    with pytest.raises(ValueError), observation.observe_project_file(
        bound_root='/tmp', relative_path='x' * 4097, baseline_sha256='a' * 64,
    ):
        pytest.fail('must not yield')
