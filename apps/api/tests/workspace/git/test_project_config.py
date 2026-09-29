"""配置子集纯解析与隔离PG/临时文件集成，不执行Git。"""

import os
from hashlib import sha256

import pytest

from app.services.workspace.git import project_config as config, project_source as source
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from tests.workspace.git.test_project_layout import repository, setup, target

__all__ = ['repository', 'setup', 'target']
VALID = b'[core]\nrepositoryformatversion = 0\nbare = false\nfilemode = true\n'


def test_supported_subset_and_case():
    settings = dict(config.parse_project_git_config(VALID + b'ignorecase = false\n'))
    assert settings['repositoryformatversion'] == '0'
    assert settings['ignorecase'] == 'false'
    assert dict(config.parse_project_git_config(VALID.upper()))['bare'] == 'false'
    assert config.parse_project_git_config(b'# comment\n; comment\n' + VALID) == config.parse_project_git_config(VALID)


@pytest.mark.parametrize('raw', [
    b'', b'\xff', b'\xef\xbb\xbf' + VALID, VALID.replace(b'\n', b'\r\n'),
    VALID + b'\x00', b'bare=false\n' + VALID, VALID + b'[core]\n',
    VALID + b'BARE=false\n', VALID.replace(b'false', b'true'),
    VALID.replace(b'= 0', b'= 1'), VALID.replace(b'false', b'0'),
    VALID.replace(b'false', b'"false"'), VALID + b'autocrlf=input\n',
    VALID + b'fsmonitor=false\n', VALID + b'worktree=/outside\n',
    VALID + b'hooksPath=/outside\n', VALID + b'unknown=true\n',
    VALID + b'[include]\npath=/outside\n', VALID + b'[includeIf "gitdir:x"]\npath=x\n',
    VALID + b'[filter "x"]\nclean=command\n', VALID + b'[extensions]\nworktreeConfig=true\n',
    VALID + b'[remote "origin"]\nurl=https://example.com\n',
    VALID.replace(b'true', b'true # comment'), VALID.replace(b'true', b'tr\\\nue'),
    b'[core]\nbare=false\n', b'[core]\nrepositoryformatversion=0\n',
])
def test_unknown_unsafe_or_unsupported_semantics_rejected(raw):
    with pytest.raises(source.ProjectGitSourceError) as caught:
        config.parse_project_git_config(raw)
    assert str(caught.value) == 'project_git_config_unsupported'


@pytest.mark.parametrize('kind', ['bytes', 'lines', 'line-bytes'])
def test_parser_budgets(kind):
    raw = {'bytes': b'#' * (config.MAX_CONFIG_BYTES + 1),
           'lines': VALID + b'\n' * config.MAX_CONFIG_LINES,
           'line-bytes': VALID + b'#' + b'x' * config.MAX_CONFIG_LINE_BYTES}[kind]
    with pytest.raises(source.ProjectGitSourceError) as caught:
        config.parse_project_git_config(raw)
    assert caught.value.code == 'project_git_config_limit'


def test_exact_byte_budget_and_bounded_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'MAX_CONFIG_BYTES', len(VALID))
    assert config.parse_project_git_config(VALID)
    path = tmp_path / 'config'
    path.write_bytes(VALID)
    with path.open('rb') as handle:
        assert config._read_bounded(handle.fileno()) == VALID
    path.write_bytes(VALID + b' ')
    with path.open('rb') as handle, pytest.raises(source.ProjectGitSourceError):
        config._read_bounded(handle.fileno())


def test_real_observation_closes_transactions_and_descriptors(setup, repository, monkeypatch):
    path = repository / 'config'
    path.write_bytes(VALID)
    original = config._read_bounded
    descriptors = []
    def checked(fd):
        assert all(session.closed and not session.in_transaction() for session in setup[2])
        descriptors.append(fd)
        return original(fd)
    monkeypatch.setattr(config, '_read_bounded', checked)
    result = config.read_project_git_config(setup[0])
    assert result.config_sha256 == sha256(VALID).hexdigest()
    assert result.settings == config.parse_project_git_config(VALID)
    assert result.scope == 'core_config_subset_only'
    assert len(descriptors) == 2
    assert path.read_bytes() == VALID
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize('change', ['content', 'same-bytes-replace', 'symlink', 'delete'])
def test_changes_during_authorization_rejected(setup, repository, monkeypatch, change):
    path = repository / 'config'
    path.write_bytes(VALID)
    original = source.read_owned_git_binding
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == 'content':
                path.write_bytes(VALID.replace(b'true', b'false'))
            elif change == 'same-bytes-replace':
                temporary = repository / 'replacement'
                temporary.write_bytes(VALID)
                temporary.replace(path)
            else:
                path.unlink()
                if change == 'symlink':
                    path.symlink_to(repository / 'HEAD')
        return original(*args, **kwargs)
    monkeypatch.setattr(source, 'read_owned_git_binding', changed)
    with pytest.raises(source.ProjectGitSourceError):
        config.read_project_git_config(setup[0])


@pytest.mark.parametrize('failure', ['read', 'parse', 'interrupt', 'oversize'])
def test_failure_releases_descriptor_without_success(setup, repository, monkeypatch, failure):
    (repository / 'config').write_bytes(VALID if failure != 'oversize' else b'x' * (config.MAX_CONFIG_BYTES + 1))
    descriptors = []
    def failed(fd):
        descriptors.append(fd)
        if failure == 'interrupt':
            raise KeyboardInterrupt()
        if failure == 'parse':
            return b'[include]\npath=/private/secret\n'
        raise PermissionError('private secret')
    monkeypatch.setattr(config, '_read_bounded', failed)
    with pytest.raises(KeyboardInterrupt if failure == 'interrupt' else source.ProjectGitSourceError) as caught:
        config.read_project_git_config(setup[0])
    assert 'private secret' not in str(caught.value)
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)
    if failure == 'oversize':
        assert not descriptors


def test_unauthorized_never_reads_config(setup, target, monkeypatch):
    monkeypatch.setattr(config, '_observe_config', lambda fd: pytest.fail('authorize first'))
    with pytest.raises(WorkspaceNotAccessibleError):
        config.read_project_git_config({**setup[0], 'user_id': target['other_id']})


@pytest.mark.parametrize('replace', [False, True])
def test_change_during_initial_read_cannot_pass(setup, repository, monkeypatch, replace):
    path = repository / 'config'
    path.write_bytes(VALID)
    read = config._read_bounded
    def changed(fd):
        raw = read(fd)
        if replace:
            other = repository / 'replacement'
            other.write_bytes(VALID)
            other.replace(path)
        else:
            path.write_bytes(VALID + b'# changed\n')
        return raw
    monkeypatch.setattr(config, '_read_bounded', changed)
    with pytest.raises(source.ProjectGitSourceError) as caught:
        config.read_project_git_config(setup[0])
    assert caught.value.code == 'project_git_config_changed'
