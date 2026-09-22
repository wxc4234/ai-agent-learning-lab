"""系统选择器协议与子进程清理边界，不在单元测试中弹出真实窗口。"""

import subprocess
from types import SimpleNamespace

import pytest

from app.services.workspace.directory import directory_picker as picker


@pytest.mark.parametrize('platform,output,expected', [
    ('darwin', 'selected:/tmp/中文 空格/\n', '/tmp/中文 空格/'),
    ('darwin', 'selected:/tmp/name\n\n', '/tmp/name\n'),
    ('darwin', 'cancelled:\n', None),
    ('win32', '{"path":"C:\\\\project"}', 'C:\\project'),
    ('win32', '{"path":null}', None),
])
def test_protocol(monkeypatch, platform, output, expected):
    monkeypatch.setattr(picker.sys, 'platform', platform)
    def run(command, **kwargs):
        assert kwargs['timeout'] == 120
        assert kwargs['check'] is True
        assert 'shell' not in kwargs
        assert command[0] == ('/usr/bin/osascript' if platform == 'darwin' else 'powershell.exe')
        return SimpleNamespace(stdout=output)
    monkeypatch.setattr(picker.subprocess, 'run', run)
    assert picker.select_directory() == expected
    assert not picker._PICKER_LOCK.locked()


@pytest.mark.parametrize('failure,code', [
    (subprocess.TimeoutExpired('PRIVATE', 120), 'directory_picker_timeout'),
    (FileNotFoundError('PRIVATE'), 'directory_picker_unavailable'),
    (subprocess.CalledProcessError(1, 'PRIVATE'), 'directory_picker_unavailable'),
])
def test_process_failure_releases_lock(monkeypatch, failure, code):
    monkeypatch.setattr(picker.sys, 'platform', 'darwin')
    def run(*args, **kwargs):
        raise failure
    monkeypatch.setattr(picker.subprocess, 'run', run)
    with pytest.raises(picker.DirectoryPickerError) as caught:
        picker.select_directory()
    assert caught.value.code == code
    assert 'PRIVATE' not in str(caught.value)
    assert not picker._PICKER_LOCK.locked()


def test_busy_and_unsupported(monkeypatch):
    with picker._PICKER_LOCK, pytest.raises(picker.DirectoryPickerError, match='directory_picker_busy'):
        picker.select_directory()
    monkeypatch.setattr(picker.sys, 'platform', 'linux')
    with pytest.raises(picker.DirectoryPickerError, match='directory_picker_unsupported'):
        picker.select_directory()
    assert not picker._PICKER_LOCK.locked()


@pytest.mark.parametrize('output', ['PRIVATE', 'selected:\n', 'selected:/tmp/\0\n'])
def test_invalid_output(monkeypatch, output):
    monkeypatch.setattr(picker.sys, 'platform', 'darwin')
    monkeypatch.setattr(picker.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=output))
    with pytest.raises(picker.DirectoryPickerError, match='directory_picker_unavailable'):
        picker.select_directory()
