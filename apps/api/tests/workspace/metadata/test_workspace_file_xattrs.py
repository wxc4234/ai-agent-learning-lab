"""真实macOS属性读取与受控ABI变化；只操作临时样例。"""

import ctypes
import errno
import os
from dataclasses import FrozenInstanceError

import pytest

from app.services.workspace.metadata import workspace_file_xattrs as service


class Attributes:
    """有限内存ABI替身，验证缓冲区预算和调用轮数，不模拟路径授权。"""

    def __init__(self, values=None):
        self.values = values if values is not None else {b'com.example.test': b'\x00\xff'}
        self.lists = 0
        self.gets = 0
        self.raw_names = None

    @staticmethod
    def fill(data, buffer, capacity):
        if buffer is None:
            return len(data)
        if len(data) > capacity:
            ctypes.set_errno(errno.ERANGE)
            return -1
        if data:
            ctypes.memmove(buffer, data, len(data))
        return len(data)

    def flistxattr(self, fd, buffer, capacity, options):
        assert options == 32
        self.lists += 1
        names = b''.join(name + b'\0' for name in self.values)
        return self.fill(names if self.raw_names is None else self.raw_names, buffer, capacity)

    def fgetxattr(self, fd, name, buffer, capacity, position, options):
        assert position == 0 and options == 32
        self.gets += 1
        return self.fill(self.values[name], buffer, capacity)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'file'
    path.write_bytes(b'original')
    with path.open('rb') as file:
        yield file.fileno(), path


@pytest.fixture
def native(monkeypatch):
    instance = Attributes()
    monkeypatch.setattr(service, '_native_library', lambda: instance)
    return instance


def test_real_binary_empty_unicode_and_descriptor_unchanged(source):
    fd, path = source
    library = service._native_library()
    library.fsetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p,
                                ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    library.fsetxattr.restype = ctypes.c_int
    expected = {b'com.example.empty': b'', 'com.example.中文'.encode(): b'\x00\xffPRIVATE'}
    for name, value in expected.items():
        assert library.fsetxattr(fd, name, value, len(value), 0, 0) == 0
    before = service._file_version(fd)
    os.lseek(fd, 3, os.SEEK_SET)
    result = service.read_file_xattrs(fd)
    actual = {item.name: item.value for item in result}
    assert all(actual[name] == value for name, value in expected.items())
    assert service._file_version(fd) == before
    assert os.lseek(fd, 0, os.SEEK_CUR) == 3 and path.read_bytes() == b'original'
    assert 'PRIVATE' not in repr(result) and 'com.example' not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result[0].value = b'changed'


@pytest.mark.parametrize('values', [{}, {b'a': b''}, {b'z': b'last', b'a': b'first'}])
def test_empty_values_order_and_fixed_two_passes(source, native, values):
    native.values = values
    result = service.read_file_xattrs(source[0])
    assert [(item.name, item.value) for item in result] == sorted(values.items())
    assert native.lists == 8 and native.gets == 6 * len(values)


@pytest.mark.parametrize('value', [None, True, -1, '3', 1.2])
def test_invalid_descriptor_before_native_access(monkeypatch, value):
    monkeypatch.setattr(service, '_native_library', lambda: pytest.fail('must validate first'))
    with pytest.raises(service.FileXattrError, match='descriptor_invalid'):
        service.read_file_xattrs(value)


@pytest.mark.parametrize('which', ['names', 'value'])
def test_oversize_rejected_before_buffer_allocation(source, native, monkeypatch, which):
    if which == 'names':
        monkeypatch.setattr(native, 'flistxattr', lambda *args: service.MAX_XATTR_NAMES_BYTES + 1)
        call = lambda: service._read_names(native, source[0])
    else:
        monkeypatch.setattr(native, 'fgetxattr', lambda *args: service.MAX_XATTR_VALUE_BYTES + 1)
        call = lambda: service._read_value(native, source[0], b'a', service.MAX_XATTR_TOTAL_BYTES)
    monkeypatch.setattr(ctypes, 'create_string_buffer', lambda *args: pytest.fail('allocation exceeds budget'))
    with pytest.raises(service.FileXattrError, match='limit_exceeded'):
        call()


@pytest.mark.parametrize('count', [128, 129])
def test_count_limit(source, native, count):
    native.values = {f'n{i}'.encode(): b'' for i in range(count)}
    if count == 128:
        assert len(service.read_file_xattrs(source[0])) == count
    else:
        with pytest.raises(service.FileXattrError, match='limit_exceeded'):
            service.read_file_xattrs(source[0])
        assert native.gets == 0


@pytest.mark.parametrize('extra', [0, 1])
def test_total_budget_includes_names(source, native, extra):
    names = [b'a', b'b', b'c', b'd']
    native.values = {name: b'x' * 65536 for name in names}
    native.values[b'd'] = b'x' * (65536 - 8 + extra)
    if extra:
        with pytest.raises(service.FileXattrError, match='limit_exceeded'):
            service.read_file_xattrs(source[0])
        assert native.gets == 10  # 第四个值仅探测长度，不分配或读取。
    else:
        result = service.read_file_xattrs(source[0])
        assert sum(len(item.name) + 1 + len(item.value) for item in result) == 262144


@pytest.mark.parametrize('raw', [b'a', b'\0', b'a\0a\0', b'\xff\0', b'x' * 128 + b'\0'])
def test_invalid_names_fail_closed(source, native, raw):
    native.raw_names = raw
    with pytest.raises(service.FileXattrError, match='invalid'):
        service.read_file_xattrs(source[0])
    assert native.gets == 0


@pytest.mark.parametrize('number,code', [
    (errno.ERANGE, 'changed'), (getattr(errno, 'ENOATTR', 93), 'changed'),
    (errno.ENOTSUP, 'unsupported'), (errno.EACCES, 'unavailable'), (errno.EIO, 'unavailable'),
])
@pytest.mark.parametrize('operation', ['flistxattr', 'fgetxattr'])
def test_native_failures_never_become_empty(source, native, monkeypatch, number, code, operation):
    def fail(*args):
        ctypes.set_errno(number)
        return -1
    monkeypatch.setattr(native, operation, fail)
    with pytest.raises(service.FileXattrError, match=f'file_xattr_{code}'):
        service.read_file_xattrs(source[0])


@pytest.mark.parametrize('when', ['name-read', 'value-read', 'value-reprobe', 'same-size', 'names-end', 'stat'])
def test_observed_changes_reject_without_retry(source, native, monkeypatch, when):
    listing = native.flistxattr
    reading = native.fgetxattr
    def changed_names(*args):
        if when == 'name-read' and native.lists == 1:
            native.values[b'new'] = b''
        if when == 'names-end' and native.lists == 2:
            native.values = {b'other': b'x'}
        return listing(*args)
    def changed_values(*args):
        if (when == 'value-read' and native.gets == 1) or (when == 'value-reprobe' and native.gets == 2):
            native.values[b'com.example.test'] = b'longer'
        if when == 'same-size' and native.gets == 3:
            native.values[b'com.example.test'] = b'zz'
        if when == 'stat' and native.gets == 0:
            source[1].write_bytes(b'external')
        return reading(*args)
    monkeypatch.setattr(native, 'flistxattr', changed_names)
    monkeypatch.setattr(native, 'fgetxattr', changed_values)
    with pytest.raises(service.FileXattrError, match='changed'):
        service.read_file_xattrs(source[0])
    assert native.lists <= 8 and native.gets <= 6


def test_closed_descriptor_is_safe_failure(source, native):
    descriptor = os.dup(source[0])
    os.close(descriptor)
    with pytest.raises(service.FileXattrError, match='unavailable'):
        service.read_file_xattrs(descriptor)


def test_directory_rejected_before_attribute_access(tmp_path, native):
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(service.FileXattrError, match='unsupported'):
            service.read_file_xattrs(descriptor)
        assert native.lists == 0
    finally:
        os.close(descriptor)


def test_platform_rejected(monkeypatch):
    service._native_library.cache_clear()
    monkeypatch.setattr(service.sys, 'platform', 'linux')
    with pytest.raises(service.FileXattrError, match='unsupported'):
        service._native_library()
    service._native_library.cache_clear()
