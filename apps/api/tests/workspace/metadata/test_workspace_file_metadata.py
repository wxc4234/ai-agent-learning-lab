"""真实 ACL/xattr 装配和受控元数据故障，不过滤系统属性。"""

import ctypes
import errno
import os
import subprocess
from types import SimpleNamespace

import pytest

from app.services.workspace.metadata import workspace_file_metadata as m
from app.services.workspace.files import workspace_file_replace as service
from tests.workspace.metadata.metadata_support import plain_metadata
from tests.workspace.files.test_workspace_file_replace import replace, sample, temps

__all__ = ['plain_metadata', 'sample']


@pytest.fixture
def opened(tmp_path):
    with (tmp_path / 'source').open('w+b') as source, (tmp_path / 'target').open('w+b') as target:
        source.write(b'old')
        source.flush()
        yield source.fileno(), target.fileno()


def test_real_environment_attributes_are_read(tmp_path):
    with (tmp_path / 'native').open('w+b') as file:
        native = m._native_library()
        native.fsetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        native.fsetxattr.restype = ctypes.c_int
        assert native.fsetxattr(file.fileno(), b'com.example.note', b'x', 1, 0, 0) == 0
        result = m.read_file_metadata(file.fileno())
        assert {item.name: item.value for item in result.xattrs}[b'com.example.note'] == b'x'


@pytest.mark.parametrize('with_acl', [False, True])
def test_native_acl_copy_and_inherited_target_replaced(opened, plain_metadata, with_acl):
    source, target = opened
    os.fchmod(source, 0o640)
    native = plain_metadata.native
    native.acl_from_text.argtypes = [ctypes.c_char_p]
    native.acl_from_text.restype = ctypes.c_void_p
    # UUID使用当前用户，避免通过名称服务解析文本中的用户名称。
    import pwd
    user = pwd.getpwuid(os.getuid()).pw_name
    output = subprocess.run(['dsmemberutil', 'getuuid', '-U', user],
                            check=True, capture_output=True, text=True)
    uuid = output.stdout.strip()
    text = f'!#acl 1\nuser:{uuid}:::allow:read\n'.encode()
    pointer = native.acl_from_text(text)
    assert pointer
    try:
        assert native.acl_set_fd_np(target, pointer, 256) == 0
        if with_acl:
            assert native.acl_set_fd_np(source, pointer, 256) == 0
    finally:
        assert native.acl_free(pointer) == 0
    expected = m.read_file_metadata(source)
    m.copy_file_metadata(source, target, expected)
    assert m.read_file_metadata(target) == expected


@pytest.mark.parametrize('attribute,result,code', [
    ('acl_size', -1, 'unsupported'), ('acl_size', 65537, 'unsupported'),
    ('acl_copy_ext', -1, 'unavailable'), ('acl_set_fd_np', -1, 'unavailable'),
])
def test_native_errors_fail_closed(opened, plain_metadata, monkeypatch, attribute, result, code):
    source, target = opened
    expected = m.read_file_metadata(source)
    monkeypatch.setattr(plain_metadata, attribute, lambda *args: result, raising=False)
    with pytest.raises(m.FileMetadataError, match=f'metadata_{code}'):
        m.copy_file_metadata(source, target, expected)


@pytest.mark.parametrize('failure_errno', [errno.EACCES, errno.ENOTSUP, errno.EIO])
def test_acl_read_error_is_not_empty_acl(opened, plain_metadata, monkeypatch, failure_errno):
    def fail(*args):
        ctypes.set_errno(failure_errno)
    monkeypatch.setattr(plain_metadata, 'acl_get_fd_np', fail, raising=False)
    with pytest.raises(m.FileMetadataError, match='unavailable'):
        m.read_file_metadata(opened[0])


@pytest.mark.parametrize('flags', [None, 2, 32768])
def test_flags_rejected(opened, plain_metadata, monkeypatch, flags):
    before = os.fstat(opened[0])
    values = {name: getattr(before, name) for name in dir(before) if name.startswith('st_')}
    values['st_flags'] = flags
    monkeypatch.setattr(m.os, 'fstat', lambda fd: SimpleNamespace(**values))
    with pytest.raises(m.FileMetadataError, match='unsupported'):
        m.read_file_metadata(opened[0])
    assert before.st_flags == 0


def test_snapshot_and_source_change(opened, plain_metadata):
    source, target = opened
    expected = m.read_file_metadata(source)
    os.fchmod(source, 0o600 if expected.mode != 0o600 else 0o640)
    with pytest.raises(m.FileMetadataError, match='changed'):
        m.copy_file_metadata(source, target, expected)


def test_target_readback_mismatch(opened, plain_metadata, monkeypatch):
    os.fchmod(opened[0], 0o640)
    os.fchmod(opened[1], 0o600)
    expected = m.read_file_metadata(opened[0])
    monkeypatch.setattr(m.os, 'fchmod', lambda *args: None)
    with pytest.raises(m.FileMetadataError, match='changed'):
        m.copy_file_metadata(*opened, expected)


@pytest.mark.parametrize('phase', ['read', 'copy', 'before', 'after'])
def test_replacement_metadata_failure_boundary(sample, monkeypatch, phase):
    original = sample[1].read_bytes()
    original_ino = sample[1].stat().st_ino
    def fail(*args):
        raise m.FileMetadataError('file_replace_metadata_changed')
    if phase in ('read', 'copy'):
        monkeypatch.setattr(service, f'{phase}_file_metadata', fail)
    else:
        verify = service.verify_file_metadata
        def check(fd, expected):
            renamed = sample[1].stat().st_ino != original_ino
            if phase == 'before' or renamed:
                fail()
            verify(fd, expected)
        monkeypatch.setattr(service, 'verify_file_metadata', check)
    result = replace(sample)
    assert result.status == ('uncertain' if phase == 'after' else 'not_replaced')
    assert result.code == ('file_replace_uncertain' if phase == 'after'
                           else 'file_replace_metadata_changed')
    assert result.cleanup_complete and not temps(sample)
    assert (sample[1].read_bytes() == original) is (phase != 'after')


def test_platform_rejected(monkeypatch):
    m._native_library.cache_clear()
    monkeypatch.setattr(m.sys, 'platform', 'linux')
    with pytest.raises(m.FileMetadataError, match='unsupported'):
        m._native_library()
    m._native_library.cache_clear()


def test_real_xattr_replacement_preserves_metadata(sample, plain_metadata):
    native = plain_metadata.native
    native.fsetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p,
                               ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    native.fsetxattr.restype = ctypes.c_int
    with sample[1].open('rb') as file:
        assert native.fsetxattr(file.fileno(), b'com.example.note', b'x', 1, 0, 0) == 0
    with sample[1].open('rb') as file:
        expected = m.read_file_metadata(file.fileno())
    result = replace(sample)
    assert result.status == 'replaced'
    with sample[1].open('rb') as file:
        assert m.read_file_metadata(file.fileno()) == expected
    assert not temps(sample)


def test_source_changes_during_metadata_read(opened, plain_metadata, monkeypatch):
    original = plain_metadata.acl_copy_ext
    def mutate(*args):
        result = original(*args)
        os.fchmod(opened[0], 0o400)
        return result
    monkeypatch.setattr(plain_metadata, 'acl_copy_ext', mutate, raising=False)
    with pytest.raises(m.FileMetadataError, match='changed'):
        m.read_file_metadata(opened[0])


def test_acl_freed_on_export_failure(opened, plain_metadata, monkeypatch):
    free = plain_metadata.acl_free
    freed = []
    def release(pointer):
        freed.append(pointer)
        return free(pointer)
    monkeypatch.setattr(plain_metadata, 'acl_free', release, raising=False)
    monkeypatch.setattr(plain_metadata, 'acl_copy_ext', lambda *args: -1, raising=False)
    with pytest.raises(m.FileMetadataError):
        m.read_file_metadata(opened[0])
    assert len(freed) == 1


def test_failed_empty_acl_allocation(opened, plain_metadata, monkeypatch):
    def absent(*args):
        ctypes.set_errno(errno.ENOENT)
    monkeypatch.setattr(plain_metadata, 'acl_get_fd_np', absent, raising=False)
    monkeypatch.setattr(plain_metadata, 'acl_init', lambda count: None, raising=False)
    with pytest.raises(m.FileMetadataError, match='unavailable'):
        m.read_file_metadata(opened[0])


def test_release_failure_is_reported(opened, plain_metadata, monkeypatch):
    free = plain_metadata.acl_free
    def release(pointer):
        assert free(pointer) == 0
        return -1
    monkeypatch.setattr(plain_metadata, 'acl_free', release, raising=False)
    with pytest.raises(m.FileMetadataError, match='unavailable'):
        m.read_file_metadata(opened[0])


def test_parent_inherited_acl_is_replaced(tmp_path, plain_metadata):
    import pwd
    directory = tmp_path / 'inherited'
    directory.mkdir()
    user = pwd.getpwuid(os.getuid()).pw_name
    subprocess.run(['chmod', '+a', f'{user} allow read,file_inherit', str(directory)], check=True)
    try:
        source = tmp_path / 'plain'
        target = directory / 'child'
        source.write_bytes(b'old')
        target.write_bytes(b'new')
        with source.open('rb') as first, target.open('rb') as second:
            expected = m.read_file_metadata(first.fileno())
            assert m.read_file_metadata(second.fileno()).acl != expected.acl
            m.copy_file_metadata(first.fileno(), second.fileno(), expected)
            assert m.read_file_metadata(second.fileno()) == expected
    finally:
        subprocess.run(['chmod', '-N', str(directory)], check=True)
