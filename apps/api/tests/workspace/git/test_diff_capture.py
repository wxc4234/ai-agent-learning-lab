"""仅在自建临时仓库验证diff；故障用真实管道验证回收边界。"""

from dataclasses import replace
import subprocess
import sys

import pytest

from app.services.workspace.git import diff_capture as diff
from app.services.workspace.git import status_capture as capture
from tests.workspace.git.test_status_capture import git, snapshot


@pytest.fixture
def sample():
    with capture.temporary_git_status_sample() as result:
        yield result
    assert not result.root.exists()


def baseline(sample):
    # 只在服务创建的临时仓库准备提交，不触碰项目索引或冻结配置。
    (sample.root / 'value.txt').write_bytes(b'value = 1\n')
    git(sample, 'add', '--all')
    git(sample, 'commit', '-qm', 'isolated diff baseline')


def test_real_scopes_preserve_files_and_index(sample):
    baseline(sample)
    (sample.root / 'value.txt').write_bytes(b'value = 2\n')
    git(sample, 'add', 'value.txt')
    (sample.root / 'value.txt').write_bytes(b'value = 3\n')
    (sample.root / 'untracked').write_bytes(b'not part of diff\n')
    before = snapshot(sample.root)
    worktree = diff.collect_sample_git_diff(sample, scope='worktree')
    staged = diff.collect_sample_git_diff(sample, scope='staged')
    assert worktree.scope == 'worktree'
    assert b'-value = 2\n+value = 3\n' in worktree.data
    assert staged.scope == 'staged'
    assert b'-value = 1\n+value = 2\n' in staged.data
    assert b'untracked' not in worktree.data + staged.data
    assert snapshot(sample.root) == before
    assert not (sample.root / '.git/index.lock').exists()


@pytest.mark.parametrize('scope', ['worktree', 'staged'])
def test_empty_diff_is_success_with_baseline(sample, scope):
    baseline(sample)
    before = snapshot(sample.root)
    assert diff.collect_sample_git_diff(sample, scope=scope).data == b''
    assert snapshot(sample.root) == before


def test_missing_head_is_failure_for_staged(sample):
    with pytest.raises(diff.GitDiffCaptureError) as caught:
        diff.collect_sample_git_diff(sample, scope='staged')
    assert caught.value.code == 'git_diff_command_failed'
    assert str(sample.root) not in str(caught.value)


@pytest.mark.parametrize('value', ['', 'HEAD', '--output=/tmp/no', None, [], 1])
def test_invalid_scope_never_spawns(sample, monkeypatch, value):
    monkeypatch.setattr(capture, '_capture', lambda *a, **k: pytest.fail('must not spawn'))
    with pytest.raises(diff.GitDiffCaptureError) as caught:
        diff.collect_sample_git_diff(sample, scope=value)
    assert caught.value.code == 'git_diff_invalid_scope'


@pytest.mark.parametrize('kind', ['copied', 'path', 'closed'])
def test_invalid_handle_never_spawns(monkeypatch, kind):
    with capture.temporary_git_status_sample() as sample:
        value = replace(sample) if kind == 'copied' else sample.root
    if kind == 'closed':
        value = sample
    monkeypatch.setattr(capture, '_capture', lambda *a, **k: pytest.fail('must not spawn'))
    with pytest.raises(diff.GitDiffCaptureError) as caught:
        diff.collect_sample_git_diff(value, scope='worktree')  # pyright: ignore[reportArgumentType] -- 故意越过静态签名，验证运行时拒绝非法输入
    assert caught.value.code == 'git_sample_unavailable'


@pytest.mark.parametrize('kind', ['config', 'missing', 'symlink', 'commondir', 'alternates'])
def test_changed_metadata_never_spawns(sample, monkeypatch, kind):
    metadata = sample.root / '.git'
    if kind == 'config':
        (metadata / 'config').write_bytes(b'changed')
    elif kind in ('missing', 'symlink'):
        saved = sample.root / 'saved-git'
        metadata.rename(saved)
        if kind == 'symlink':
            metadata.symlink_to(saved, target_is_directory=True)
    else:
        name = 'commondir' if kind == 'commondir' else 'objects/info/alternates'
        (metadata / name).write_text('/PRIVATE\n')
    monkeypatch.setattr(capture, '_capture', lambda *a, **k: pytest.fail('must not spawn'))
    with pytest.raises(diff.GitDiffCaptureError) as caught:
        diff.collect_sample_git_diff(sample, scope='worktree')
    assert caught.value.code == 'git_sample_unavailable'


@pytest.mark.parametrize('script,code', [
    ('import time; time.sleep(30)', 'git_diff_timeout'),
    ('import os,time; os.close(1); os.close(2); time.sleep(30)', 'git_diff_timeout'),
    ('import os; os.write(1,b"x"*300000)', 'git_diff_output_limit'),
    ('import os; os.write(2,b"PRIVATE"*10000)', 'git_diff_output_limit'),
    ('import sys; sys.stdout.write("partial"); sys.stderr.write("PRIVATE"); sys.exit(1)',
     'git_diff_command_failed'),
])
def test_failures_never_return_partial_result(sample, monkeypatch, script, code):
    original = subprocess.Popen
    children = []

    def spawn(argv, **kwargs):
        # 仅替换子进程程序，实际排空、预算、等待和清理代码仍参与测试。
        process = original((sys.executable, '-c', script), **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(capture.subprocess, 'Popen', spawn)
    monkeypatch.setattr(capture, 'STATUS_TIMEOUT_SECONDS', 0.3)
    with pytest.raises(diff.GitDiffCaptureError) as caught:
        diff.collect_sample_git_diff(sample, scope='worktree')
    assert caught.value.code == code
    assert 'PRIVATE' not in str(caught.value)
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].stdout.closed and children[0].stderr.closed


def test_spawn_failure_is_sanitized(sample, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('PRIVATE')

    monkeypatch.setattr(capture.subprocess, 'Popen', fail)
    with pytest.raises(diff.GitDiffCaptureError) as caught:
        diff.collect_sample_git_diff(sample, scope='worktree')
    assert caught.value.code == 'git_diff_unavailable'
    assert 'PRIVATE' not in str(caught.value)


@pytest.mark.parametrize('scope', ['worktree', 'staged'])
def test_fixed_command_environment_and_raw_bytes(sample, monkeypatch, scope):
    original = subprocess.Popen
    calls = []
    for name in ('GIT_DIR', 'GIT_INDEX_FILE', 'GIT_EXTERNAL_DIFF', 'GIT_CONFIG_PARAMETERS', 'GIT_TRACE'):
        monkeypatch.setenv(name, '/PRIVATE')

    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        script = 'import os; os.write(2,b"x"*10000); os.write(1,b"\\xff\\x00")'
        return original((sys.executable, '-c', script), **kwargs)

    monkeypatch.setattr(capture.subprocess, 'Popen', spawn)
    assert diff.collect_sample_git_diff(sample, scope=scope).data == b'\xff\x00'
    argv, options = calls[0]
    assert argv[0] == '/usr/bin/git'
    assert {'--no-ext-diff', '--no-textconv', '--no-optional-locks', '--no-pager'} <= set(argv)
    assert f'--git-dir={sample.root}/.git' in argv
    assert f'--work-tree={sample.root}' in argv
    assert argv[-3:] == ('--cached', 'HEAD', '--') if scope == 'staged' else argv[-1:] == ('--',)
    assert '--exit-code' not in argv and '--quiet' not in argv
    assert options['cwd'] == sample.root
    assert options['env']['GIT_CONFIG_GLOBAL'] == '/dev/null'
    assert options['env']['GIT_OPTIONAL_LOCKS'] == '0'
    assert '/PRIVATE' not in options['env'].values()
    assert options['stdin'] == subprocess.DEVNULL
    assert options['start_new_session'] is True and 'shell' not in options
