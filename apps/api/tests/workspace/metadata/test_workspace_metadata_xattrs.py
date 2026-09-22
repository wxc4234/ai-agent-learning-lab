"""真实临时文件的完整快照、装配顺序和失败边界。"""
import ctypes
import os

import pytest

from app.services.workspace.metadata import workspace_file_metadata as m
from app.services.workspace.metadata import workspace_xattr_copy as x
from tests.workspace.metadata.metadata_support import NativeProxy
from tests.workspace.metadata.test_workspace_file_metadata import opened

__all__ = ['opened']


def set_attr(fd, name=b'com.example.lesson', value=b'\x00\xff'):
    buffer = ctypes.create_string_buffer(value, max(1, len(value)))
    assert x._native_library().fsetxattr(fd, name, buffer, len(value), 0, 0) == 0


def test_real_complete_copy(opened):
    source, target = opened
    set_attr(source)
    set_attr(source, b'com.example.empty', b'')
    expected = m.read_file_metadata(source)
    before = [(os.pread(fd, 32, 0), os.lseek(fd, 0, 1)) for fd in opened]
    m.copy_file_metadata(source, target, expected)
    assert m.read_file_metadata(target) == expected
    assert [(os.pread(fd, 32, 0), os.lseek(fd, 0, 1)) for fd in opened] == before
    assert 'com.example' not in repr(expected)


@pytest.mark.parametrize('duplicate', [False, True])
def test_same_inode_refused_before_write(opened, monkeypatch, duplicate):
    source = opened[0]
    target = os.dup(source) if duplicate else source
    expected = m.read_file_metadata(source)
    monkeypatch.setattr(m.os, 'fchmod', lambda *args: pytest.fail('must not write'))
    try:
        with pytest.raises(m.FileMetadataError, match='unsupported'):
            m.copy_file_metadata(source, target, expected)
    finally:
        if duplicate:
            os.close(target)


def test_extra_target_refused_before_permissions(opened, monkeypatch):
    set_attr(opened[1])
    expected = m.read_file_metadata(opened[0])
    monkeypatch.setattr(m.os, 'fchmod', lambda *args: pytest.fail('must not write'))
    with pytest.raises(m.FileMetadataError, match='unsupported'):
        m.copy_file_metadata(*opened, expected)


@pytest.mark.parametrize('where', [0, 1])
def test_change_after_xattr_copy_rejected(opened, monkeypatch, where):
    expected = m.read_file_metadata(opened[0])
    original = m.copy_file_xattrs
    def change(**kwargs):
        original(**kwargs)
        set_attr(opened[where])
    monkeypatch.setattr(m, 'copy_file_xattrs', change)
    with pytest.raises(m.FileMetadataError, match='changed'):
        m.copy_file_metadata(*opened, expected)


@pytest.mark.parametrize('error', [m.FileXattrError('private'), x.XattrCopyError('private'), OSError('private'), KeyboardInterrupt()])
def test_partial_failure_keeps_fds(opened, monkeypatch, error):
    expected = m.read_file_metadata(opened[0])
    def fail(**kwargs):
        set_attr(opened[1])
        raise error
    monkeypatch.setattr(m, 'copy_file_xattrs', fail)
    proxy = NativeProxy(m._native_library())
    proxy.acl_set_fd_np = lambda *args: pytest.fail('ACL must follow xattrs')
    monkeypatch.setattr(m, '_native_library', lambda: proxy)
    with pytest.raises(KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else m.FileMetadataError) as caught:
        m.copy_file_metadata(*opened, expected)
    assert 'private' not in str(caught.value)
    assert any(item.name == b'com.example.lesson' for item in m.read_file_xattrs(opened[1]))
    for fd in opened:
        os.fstat(fd)


def test_acl_invalidates_xattrs_detected(opened, monkeypatch):
    expected = m.read_file_metadata(opened[0])
    proxy = NativeProxy(m._native_library())
    original = proxy.acl_set_fd_np
    def change(fd, pointer, kind):
        result = original(fd, pointer, kind)
        set_attr(fd)
        return result
    proxy.acl_set_fd_np = change
    monkeypatch.setattr(m, '_native_library', lambda: proxy)
    with pytest.raises(m.FileMetadataError, match='changed'):
        m.copy_file_metadata(*opened, expected)


def test_read_error_is_not_empty(opened, monkeypatch):
    def fail(fd):
        raise m.FileXattrError('private')
    monkeypatch.setattr(m, 'read_file_xattrs', fail)
    with pytest.raises(m.FileMetadataError, match='unavailable'):
        m.read_file_metadata(opened[0])


def test_two_rounds_must_match(opened, monkeypatch):
    original = m.read_file_xattrs
    calls = []
    def change(fd):
        result = original(fd)
        calls.append(fd)
        if len(calls) == 2:
            return result + (m.FileXattr(b'com.example.synthetic', b'value'),)
        return result
    monkeypatch.setattr(m, 'read_file_xattrs', change)
    with pytest.raises(m.FileMetadataError, match='changed'):
        m.read_file_metadata(opened[0])
    assert len(calls) == 2


def test_permissions_xattrs_acl_order(opened, monkeypatch):
    expected = m.read_file_metadata(opened[0])
    calls = []
    chmod = m.os.fchmod
    copy = m.copy_file_xattrs
    proxy = NativeProxy(m._native_library())
    acl = proxy.acl_set_fd_np
    def mode(*args):
        calls.append('mode')
        return chmod(*args)
    def attributes(**kwargs):
        calls.append('xattrs')
        return copy(**kwargs)
    def access(*args):
        calls.append('acl')
        return acl(*args)
    monkeypatch.setattr(m.os, 'fchmod', mode)
    monkeypatch.setattr(m, 'copy_file_xattrs', attributes)
    proxy.acl_set_fd_np = access
    monkeypatch.setattr(m, '_native_library', lambda: proxy)
    m.copy_file_metadata(*opened, expected)
    assert calls == ['mode', 'xattrs', 'acl']


@pytest.mark.parametrize('where', [0, 1])
def test_content_change_during_copy_rejected(opened, monkeypatch, where):
    expected = m.read_file_metadata(opened[0])
    original = m.copy_file_xattrs
    def change(**kwargs):
        original(**kwargs)
        os.pwrite(opened[where], b'changed-content', 0)
    monkeypatch.setattr(m, 'copy_file_xattrs', change)
    with pytest.raises(m.FileMetadataError, match='changed'):
        m.copy_file_metadata(*opened, expected)
