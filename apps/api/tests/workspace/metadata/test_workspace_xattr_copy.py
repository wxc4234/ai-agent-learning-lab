"""受限复制：真实临时文件与受控属性故障，不接已有replace。"""

import ctypes
import os
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from app.services.workspace.metadata import workspace_xattr_copy as service
from app.services.workspace.metadata.workspace_file_xattrs import FileXattr, FileXattrError, read_file_xattrs


@pytest.fixture
def files(tmp_path):
    first = tmp_path / 'source'
    second = tmp_path / 'target'
    first.write_bytes(b'original')
    second.write_bytes(b'new content')
    with first.open('rb') as source, second.open('r+b') as target:
        yield source.fileno(), target.fileno(), first, second


def test_real_copy_and_noop_preserve_content_and_descriptors(files):
    source, target, first, second = files
    native = service._native_library()
    values = {b'com.example.copy.empty': b'', b'com.example.copy.binary': b'\x00\xffsecret'}
    for name, value in values.items():
        assert native.fsetxattr(source, name, value, len(value), 0, 0) == 0
    expected = read_file_xattrs(source)
    os.lseek(source, 2, os.SEEK_SET)
    os.lseek(target, 3, os.SEEK_SET)
    result = service.copy_file_xattrs(source_fd=source, target_fd=target, expected=expected)
    assert result.written_count >= 2 and result.verified_count == len(expected)
    assert read_file_xattrs(target) == expected
    assert first.read_bytes() == b'original' and second.read_bytes() == b'new content'
    assert os.lseek(source, 0, os.SEEK_CUR) == 2 and os.lseek(target, 0, os.SEEK_CUR) == 3
    assert service.copy_file_xattrs(source_fd=source, target_fd=target, expected=expected).written_count == 0
    assert 'secret' not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result.written_count = 999


@pytest.fixture
def harness(files, monkeypatch):
    source, target, _, _ = files
    expected = (FileXattr(b'a', b'one'), FileXattr(b'b', b''))
    data = {source: {item.name: item.value for item in expected}, target: {}}
    calls = []
    def read(fd):
        return tuple(FileXattr(name, value) for name, value in sorted(data[fd].items()))
    def write(fd, name, buffer, size, position, options):
        assert fd == target and position == 0 and options == 0
        calls.append(name)
        data[fd][name] = ctypes.string_at(buffer, size)
        return 0
    native = SimpleNamespace(fsetxattr=write)
    monkeypatch.setattr(service, '_native_library', lambda: native)
    monkeypatch.setattr(service, 'read_file_xattrs', read)
    return SimpleNamespace(data=data, calls=calls, native=native, expected=expected, read=read,
                           args={'source_fd': source, 'target_fd': target, 'expected': expected})


@pytest.mark.parametrize('kind', ['same', 'duplicate', 'directory', 'hardlink'])
def test_unsafe_target_rejected(files, harness, kind):
    source, _target, first, second = files
    opened = None
    if kind == 'same':
        harness.args['target_fd'] = source
    elif kind == 'duplicate':
        opened = os.dup(source)
        harness.args['target_fd'] = opened
    elif kind == 'directory':
        opened = os.open(first.parent, os.O_RDONLY | os.O_DIRECTORY)
        harness.args['target_fd'] = opened
    else:
        os.link(second, first.parent / 'another')
    try:
        with pytest.raises(service.XattrCopyError, match='unsupported'):
            service.copy_file_xattrs(**harness.args)
        assert not harness.calls
    finally:
        if opened is not None:
            os.close(opened)


@pytest.mark.parametrize('kind', ['stale', 'extra', 'already-equal'])
def test_preconditions_and_equal_values(files, harness, kind):
    source, target, _, _ = files
    if kind == 'stale':
        harness.args['expected'] = ()
    elif kind == 'extra':
        harness.data[target][b'extra'] = b'PRIVATE'
    else:
        harness.data[target] = dict(harness.data[source])
    if kind == 'already-equal':
        assert service.copy_file_xattrs(**harness.args).written_count == 0
    else:
        with pytest.raises(service.XattrCopyError):
            service.copy_file_xattrs(**harness.args)
    assert not harness.calls


@pytest.mark.parametrize('kind', ['fail-second', 'noop', 'extra-after', 'source-value', 'source-content', 'target-content', 'read-failure'])
def test_failure_never_rolls_back_or_changes_source_itself(files, harness, monkeypatch, kind):
    source, target, first, second = files
    original = harness.native.fsetxattr
    def write(*args):
        if kind == 'fail-second' and harness.calls:
            return -1
        if kind == 'noop':
            return 0
        result = original(*args)
        if kind == 'extra-after':
            harness.data[target][b'extra'] = b'PRIVATE'
        elif kind == 'source-value':
            harness.data[source][b'a'] = b'changed'
        elif kind == 'source-content':
            first.write_bytes(b'external')
        elif kind == 'target-content':
            second.write_bytes(b'external')
        elif kind == 'read-failure':
            def fail(fd):
                raise FileXattrError('file_xattr_unavailable')
            monkeypatch.setattr(service, 'read_file_xattrs', fail)
        return result
    monkeypatch.setattr(harness.native, 'fsetxattr', write)
    with pytest.raises((service.XattrCopyError, FileXattrError)):
        service.copy_file_xattrs(**harness.args)
    assert first.read_bytes() == (b'external' if kind == 'source-content' else b'original')
    if kind == 'fail-second':
        assert harness.data[target] == {b'a': b'one'}
        assert harness.calls == [b'a']
    os.fstat(source)
    os.fstat(target)


@pytest.mark.parametrize('kind', ['source', 'target'])
def test_change_between_initial_reads_and_first_write(files, harness, monkeypatch, kind):
    count = 0
    def read(fd):
        nonlocal count
        count += 1
        if count == 3:
            harness.data[files[0] if kind == 'source' else files[1]][b'new'] = b'x'
        return harness.read(fd)
    monkeypatch.setattr(service, 'read_file_xattrs', read)
    with pytest.raises(service.XattrCopyError, match='snapshot_changed'):
        service.copy_file_xattrs(**harness.args)
    assert not harness.calls


@pytest.mark.parametrize('field,value', [('source_fd', True), ('target_fd', -1), ('expected', []), ('expected', (None,))])
def test_invalid_inputs_before_native_access(harness, monkeypatch, field, value):
    monkeypatch.setattr(service, '_native_library', lambda: pytest.fail('must validate first'))
    with pytest.raises(service.XattrCopyError, match='input_invalid'):
        service.copy_file_xattrs(**(harness.args | {field: value}))


@pytest.mark.parametrize('field', ['st_uid', 'st_flags', 'st_mode'])
def test_target_permission_policy_before_writes(files, harness, monkeypatch, field):
    original = os.fstat
    def metadata(fd):
        result = original(fd)
        if fd != files[1]:
            return result
        names = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_uid', 'st_flags')
        data = {name: getattr(result, name) for name in names}
        data[field] = result.st_uid + 1 if field == 'st_uid' else 2 if field == 'st_flags' else result.st_mode | 0o4000
        return SimpleNamespace(**data)
    monkeypatch.setattr(os, 'fstat', metadata)
    with pytest.raises(service.XattrCopyError, match='unsupported'):
        service.copy_file_xattrs(**harness.args)
    assert not harness.calls


@pytest.mark.parametrize('error_type', [OSError, KeyboardInterrupt])
def test_native_exception_does_not_close_caller_descriptors(files, harness, monkeypatch, error_type):
    def fail(*args):
        raise error_type('PRIVATE')
    monkeypatch.setattr(harness.native, 'fsetxattr', fail)
    with pytest.raises(service.XattrCopyError if error_type is OSError else KeyboardInterrupt) as caught:
        service.copy_file_xattrs(**harness.args)
    if error_type is OSError:
        assert str(caught.value) == 'xattr_copy_unavailable'
    os.fstat(files[0])
    os.fstat(files[1])
