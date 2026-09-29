"""隔离PG与手工临时布局；不启动Git，也不读取元数据文件正文。"""

import os

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.git import project_layout as layout, project_source as source
from tests.workspace.git.test_project_source import setup, target
from tests.assertions import require_value

__all__ = ['setup', 'target']


@pytest.fixture
def repository(setup):
    git = setup[1] / '.git'
    git.mkdir()
    (git / 'objects/info').mkdir(parents=True)
    (git / 'refs/heads').mkdir(parents=True)
    (git / 'HEAD').write_text('ref: refs/heads/main\n')
    # 危险配置正文也不会在本层执行或被误称为已通过配置审查。
    (git / 'config').write_text('[core]\nfsmonitor = untrusted-command\n')
    return git


def test_layout_success_scans_without_transaction_or_file_content(setup, repository, monkeypatch):
    scan = layout._scan
    calls = []
    def checked(fd):
        assert all(session.closed and not session.in_transaction() for session in setup[2])
        calls.append(fd)
        return scan(fd)
    monkeypatch.setattr(layout, '_scan', checked)
    monkeypatch.setattr(os, 'read', lambda *args: pytest.fail('no file content reads'))
    before = (repository / 'config').stat()
    result = layout.read_project_git_layout(setup[0])
    assert result.scope == 'metadata_layout_only'
    assert result.entry_count == 6
    assert result.git_identity == (repository.stat().st_dev, repository.stat().st_ino)
    assert len(calls) == 2 and calls[0] == calls[1]
    with pytest.raises(OSError):
        os.fstat(calls[0])
    assert (repository / 'config').stat().st_mtime_ns == before.st_mtime_ns
    assert str(repository) not in repr(result)


@pytest.mark.parametrize('kind', ['missing', 'pointer', 'symlink', 'dangling'])
def test_git_entry_refused(setup, kind):
    git = setup[1] / '.git'
    if kind == 'pointer':
        git.write_text('gitdir: /private/outside')
    elif kind in ('symlink', 'dangling'):
        git.symlink_to(setup[1] if kind == 'symlink' else setup[1] / 'missing')
    with pytest.raises(source.ProjectGitSourceError) as caught:
        layout.read_project_git_layout(setup[0])
    assert caught.value.code == ('project_git_layout_missing' if kind == 'missing' else 'project_git_layout_unsupported')


@pytest.mark.parametrize('name', ['commondir', 'gitdir', 'config.worktree', 'worktrees', 'modules', 'objects/info/alternates', 'objects/info/http-alternates'])
def test_indirect_metadata_rejected_even_when_empty(setup, repository, name):
    (repository / name).touch()
    with pytest.raises(source.ProjectGitSourceError) as caught:
        layout.read_project_git_layout(setup[0])
    assert caught.value.code == 'project_git_layout_unsupported'


@pytest.mark.parametrize('kind', ['nested-link', 'fifo', 'hardlink', 'missing-head', 'objects-file'])
def test_invalid_metadata_shapes(setup, repository, kind):
    if kind == 'nested-link':
        (repository / 'refs/heads/main').symlink_to('/private/elsewhere')
    elif kind == 'fifo':
        os.mkfifo(repository / 'index')
    elif kind == 'hardlink':
        os.link(repository / 'HEAD', repository / 'index')
    elif kind == 'missing-head':
        (repository / 'HEAD').unlink()
    else:
        (repository / 'objects/info').rmdir()
        (repository / 'objects').rmdir()
        (repository / 'objects').touch()
    with pytest.raises(source.ProjectGitSourceError) as caught:
        layout.read_project_git_layout(setup[0])
    assert caught.value.code == 'project_git_layout_unsupported'


@pytest.mark.parametrize('kind', ['entries', 'depth'])
def test_budget_rejection(setup, repository, monkeypatch, kind):
    monkeypatch.setattr(layout, 'MAX_LAYOUT_ENTRIES' if kind == 'entries' else 'MAX_LAYOUT_DEPTH', 1)
    with pytest.raises(source.ProjectGitSourceError) as caught:
        layout.read_project_git_layout(setup[0])
    assert caught.value.code == 'project_git_layout_limit'


def test_entry_budget_exact_boundary(setup, repository, monkeypatch):
    monkeypatch.setattr(layout, 'MAX_LAYOUT_ENTRIES', 6)
    assert layout.read_project_git_layout(setup[0]).entry_count == 6
    (repository / 'index').touch()
    with pytest.raises(source.ProjectGitSourceError) as caught:
        layout.read_project_git_layout(setup[0])
    assert caught.value.code == 'project_git_layout_limit'


@pytest.mark.parametrize('change', ['git', 'nested', 'file', 'alternates', 'binding'])
def test_changes_at_second_authorization_are_rejected(setup, repository, engine, monkeypatch, change):
    original = source.read_owned_git_binding
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == 'git':
                repository.rename(repository.with_name('old-git'))
                repository.mkdir()
            elif change == 'nested':
                (repository / 'refs/heads').rename(repository / 'refs/old')
                (repository / 'refs/heads').mkdir()
            elif change == 'file':
                (repository / 'config').write_text('changed')
            elif change == 'alternates':
                (repository / 'objects/info/alternates').touch()
            else:
                with Session(engine) as session, session.begin():
                    require_value(session.scalar(select(Workspace))).binding_revision += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(source, 'read_owned_git_binding', changed)
    with pytest.raises(source.ProjectGitSourceError):
        layout.read_project_git_layout(setup[0])


def test_unauthorized_does_not_inspect(setup, target, monkeypatch):
    reference = {**setup[0], 'user_id': target['other_id']}
    monkeypatch.setattr(layout, '_observe_layout', lambda root: pytest.fail('authorization first'))
    with pytest.raises(WorkspaceNotAccessibleError):
        layout.read_project_git_layout(reference)


@pytest.mark.parametrize('stage', [1, 2])
def test_scan_failures_are_unknown_and_release_git_descriptor(setup, repository, monkeypatch, stage):
    scan = layout._scan
    calls = []
    def failed(fd):
        calls.append(fd)
        if len(calls) == stage:
            raise PermissionError('private metadata path')
        return scan(fd)
    monkeypatch.setattr(layout, '_scan', failed)
    with pytest.raises(source.ProjectGitSourceError) as caught:
        layout.read_project_git_layout(setup[0])
    assert str(caught.value) == 'project_git_source_unavailable'
    for fd in set(calls):
        with pytest.raises(OSError):
            os.fstat(fd)


def test_interrupt_is_preserved_and_descriptor_closed(setup, repository, monkeypatch):
    descriptors = []
    def interrupted(fd):
        descriptors.append(fd)
        raise KeyboardInterrupt()
    monkeypatch.setattr(layout, '_scan', interrupted)
    with pytest.raises(KeyboardInterrupt):
        layout.read_project_git_layout(setup[0])
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
