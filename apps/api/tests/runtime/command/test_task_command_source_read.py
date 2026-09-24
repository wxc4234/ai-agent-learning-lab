"""真实目录/描述符验证固定文件读取，独立于数据库授权集成测试。"""

import os
from dataclasses import replace

import pytest

from app.services.runtime.command import task_command_source as service
from app.services.workspace.files.workspace_file import MAX_TEXT_FILE_BYTES, WorkspaceFileError
from app.services.workspace.samples.temporary_proposal_sample import temporary_proposal_sample
from app.tools.context import ToolExecutionContext


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr('tempfile.gettempdir', lambda: str(tmp_path.resolve()))
    with temporary_proposal_sample() as sample:
        value = service.TaskCommandSource(
            context=ToolExecutionContext(1, 'conversation', 'workspace', 'task'),
            _sample=sample, _lifetime=service._Lifetime(),
        )
        yield value
        value._lifetime.active = False


@pytest.mark.parametrize('content', [b'', b'old\n', '中文\r\n'.encode(), b'\xef\xbb\xbfhello\r\n', b'\x00\xff', b'x' * MAX_TEXT_FILE_BYTES])
def test_exact_bytes_and_source_metadata_unchanged(source, content):
    path = source.root / 'example.txt'
    path.write_bytes(content)
    before = path.stat()
    assert source.read_sample_bytes() == content
    after = path.stat()
    assert (after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns) == (
        before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns,
    )


def test_oversize_rejected(source):
    (source.root / 'example.txt').write_bytes(b'x' * (MAX_TEXT_FILE_BYTES + 1))
    with pytest.raises(WorkspaceFileError) as caught:
        source.read_sample_bytes()
    assert caught.value.code == 'file_too_large'


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'directory', 'mode', 'missing'])
def test_invalid_file_rejected_without_following(source, tmp_path, kind):
    path = source.root / 'example.txt'
    saved = path.read_bytes()
    external = tmp_path / 'outside.txt'
    external.write_bytes(b'outside untouched')
    path.unlink()
    if kind == 'symlink':
        path.symlink_to(external)
    elif kind == 'hardlink':
        os.link(external, path)
    elif kind == 'fifo':
        os.mkfifo(path)
    elif kind == 'directory':
        path.mkdir()
    elif kind == 'mode':
        path.write_bytes(saved)
        path.chmod(0o644)
    try:
        with pytest.raises(service.TaskCommandSourceUnavailable):
            source.read_sample_bytes()
        assert external.read_bytes() == b'outside untouched'
    finally:
        if kind == 'directory':
            path.rmdir()
        elif kind != 'missing':
            path.unlink()
        path.write_bytes(saved)
        path.chmod(0o600)


@pytest.mark.parametrize('kind', ['content', 'replace', 'root'])
def test_change_during_read_is_rejected(source, monkeypatch, kind):
    root = source.root
    path = root / 'example.txt'
    held = root.with_name(root.name + '-held')
    original = service._read_bounded_bytes
    def changed(fd):
        content = original(fd)
        if kind == 'content':
            path.write_bytes(b'changed and longer')
        elif kind == 'replace':
            replacement = root / 'replacement'
            replacement.write_bytes(content)
            replacement.chmod(0o600)
            replacement.replace(path)
        else:
            root.rename(held)
            root.mkdir(mode=0o700)
        return content
    monkeypatch.setattr(service, '_read_bounded_bytes', changed)
    try:
        with pytest.raises(service.TaskCommandSourceUnavailable):
            source.read_sample_bytes()
    finally:
        if kind == 'root':
            root.rmdir()
            held.rename(root)


@pytest.mark.parametrize('failed', [False, True])
def test_descriptors_closed_on_success_and_error(source, monkeypatch, failed):
    closed = []
    original = os.close
    def close(fd):
        original(fd)
        closed.append(fd)
    monkeypatch.setattr(service.os, 'close', close)
    if failed:
        def deny(fd):
            raise PermissionError('PRIVATE source path')
        monkeypatch.setattr(service, '_read_bounded_bytes', deny)
        with pytest.raises(service.TaskCommandSourceUnavailable) as caught:
            source.read_sample_bytes()
        assert 'PRIVATE' not in str(caught.value)
    else:
        assert source.read_sample_bytes() == b'old\n'
    assert closed
    for fd in closed:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize('kind', ['inactive', 'fork', 'parent', 'relative', 'parent_reference'])
def test_invalid_lifetime_or_root_rejected(source, kind):
    if kind == 'inactive':
        source._lifetime.active = False
    elif kind == 'fork':
        source._lifetime.pid += 1
    elif kind == 'parent':
        source = replace(source, _sample=replace(source._sample, parent_identity=(-1, -1)))
    elif kind == 'relative':
        source = replace(source, _sample=replace(source._sample, root=source.root.relative_to('/')))
    else:
        source = replace(source, _sample=replace(source._sample, root=source.root / '..' / source.root.name))
    with pytest.raises(service.TaskCommandSourceUnavailable):
        source.read_sample_bytes()
