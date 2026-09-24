"""仅在自建临时仓库准备Git历史；不暂存/提交用户项目。"""

from dataclasses import replace
import subprocess
import sys

import pytest

from app.services.workspace.git import status_capture as capture
from app.services.workspace.git.status_parser import GitStatusParseError


@pytest.fixture
def sample():
    with capture.temporary_git_status_sample() as result:
        yield result
    assert not result.root.exists()


def git(sample, *args):
    # 夹具命令只用于自建仓库的准备；身份在命令行提供，不改冻结配置。
    return subprocess.run((capture.GIT_EXECUTABLE, '-c', 'user.name=Fixture',
                           '-c', 'user.email=fixture@example.invalid', *args),
                          cwd=sample.root, env=capture._environment(sample.root.parent),
                          check=True, capture_output=True, timeout=5).stdout


def snapshot(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
            for path in root.rglob('*') if path.is_file()}


def test_real_status_and_no_index_or_file_changes(sample, monkeypatch):
    root = sample.root
    for name in ('modified', 'deleted', 'renamed'):
        (root / name).write_text(name + '\n')
    git(sample, 'add', '--all')
    git(sample, 'commit', '-qm', 'isolated fixture baseline')
    (root / 'modified').write_text('staged\n')
    git(sample, 'add', 'modified')
    (root / 'modified').write_text('unstaged\n')
    (root / 'deleted').unlink()
    git(sample, 'mv', 'renamed', '中文 name\nnew')
    (root / 'untracked -> name').write_text('new\n')
    # 不可信宿主环境不能重定向索引、仓库、配置或触发trace写入。
    for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_CONFIG_PARAMETERS', 'GIT_TRACE'):
        monkeypatch.setenv(name, '/PRIVATE/not-allowed')
    before = snapshot(root)
    result = capture.collect_sample_git_status(sample)
    entries = {entry.path: entry for entry in result.entries}
    assert entries['modified'].xy == 'MM'
    assert entries['deleted'].xy == ' D'
    assert entries['中文 name\nnew'].original_path == 'renamed'
    assert entries['untracked -> name'].kind == 'untracked'
    assert snapshot(root) == before
    assert not (root / '.git/index.lock').exists()


def test_empty_unborn_repository(sample):
    before = snapshot(sample.root)
    assert capture.collect_sample_git_status(sample).entries == ()
    assert snapshot(sample.root) == before


@pytest.mark.parametrize('kind', ['copied', 'path', 'closed'])
def test_only_live_object_handle(kind):
    with capture.temporary_git_status_sample() as sample:
        value = replace(sample) if kind == 'copied' else sample.root if kind == 'path' else sample
        if kind != 'closed':
            with pytest.raises(capture.GitStatusCaptureError):
                capture.collect_sample_git_status(value)
    if kind == 'closed':
        with pytest.raises(capture.GitStatusCaptureError):
            capture.collect_sample_git_status(value)


@pytest.mark.parametrize('kind', ['config', 'gitfile', 'symlink', 'missing', 'commondir', 'alternates'])
def test_changed_metadata_rejected_before_process(sample, monkeypatch, kind):
    metadata = sample.root / '.git'
    if kind == 'config':
        with (metadata / 'config').open('a') as stream:
            stream.write('\n[core]\nfsmonitor = /PRIVATE/command\n')
    elif kind in ('gitfile', 'symlink', 'missing'):
        saved = metadata.with_name('saved-git')
        metadata.rename(saved)
        if kind == 'gitfile':
            metadata.write_text('gitdir: /PRIVATE\n')
        elif kind == 'symlink':
            metadata.symlink_to(saved, target_is_directory=True)
    else:
        (metadata / ('commondir' if kind == 'commondir' else 'objects/info/alternates')).write_text('/PRIVATE\n')
    monkeypatch.setattr(capture, '_capture', lambda *args, **kwargs: pytest.fail('must not spawn'))
    with pytest.raises(capture.GitStatusCaptureError) as caught:
        capture.collect_sample_git_status(sample)
    assert caught.value.code == 'git_sample_unavailable'


def test_no_parent_repository_discovery(sample):
    # 父目录也初始化仓库，子目录缺少.git时不能读取父仓库状态。
    subprocess.run((capture.GIT_EXECUTABLE, 'init', '-q', '--template=', str(sample.root.parent)),
                   env=capture._environment(sample.root.parent), check=True, capture_output=True, timeout=5)
    (sample.root / '.git').rename(sample.root / 'hidden')
    with pytest.raises(capture.GitStatusCaptureError) as caught:
        capture.collect_sample_git_status(sample)
    assert caught.value.code == 'git_sample_unavailable'


def test_real_nonzero_is_not_empty_success(sample):
    (sample.root / '.git/HEAD').write_text('not a ref\n')
    with pytest.raises(capture.GitStatusCaptureError) as caught:
        capture.collect_sample_git_status(sample)
    assert caught.value.code == 'git_status_command_failed'
    assert str(sample.root) not in str(caught.value)


@pytest.mark.parametrize('script,code', [
    ('import time; time.sleep(30)', 'git_status_timeout'),
    ('import os,time; os.close(1); os.close(2); time.sleep(30)', 'git_status_timeout'),
    ('import os; os.write(1,b"x"*300000)', 'git_status_output_limit'),
    ('import os; os.write(2,b"PRIVATE"*10000)', 'git_status_output_limit'),
    ('import sys; sys.stderr.write("PRIVATE"); sys.exit(1)', 'git_status_command_failed'),
])
def test_process_failure_bounded_and_reaped(sample, monkeypatch, script, code):
    original = subprocess.Popen
    children = []
    def spawn(argv, **kwargs):
        # 仅测试替换固定Git子进程，实际管道读取/超时/进程回收逻辑不替换。
        process = original((sys.executable, '-c', script), **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(capture.subprocess, 'Popen', spawn)
    monkeypatch.setattr(capture, 'STATUS_TIMEOUT_SECONDS', 0.3)
    with pytest.raises(capture.GitStatusCaptureError) as caught:
        capture.collect_sample_git_status(sample)
    assert caught.value.code == code and 'PRIVATE' not in str(caught.value)
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].stdout.closed and children[0].stderr.closed


def test_fixed_command_environment_and_two_pipe_drain(sample, monkeypatch):
    original = subprocess.Popen
    calls = []
    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        # 两路均真实写入并读到EOF，stderr保持本服务预算以内。
        script = 'import os; os.write(2,b"x"*10000); os.write(1,b"?? file\\0")'
        return original((sys.executable, '-c', script), **kwargs)
    monkeypatch.setattr(capture.subprocess, 'Popen', spawn)
    assert capture.collect_sample_git_status(sample).entries[0].path == 'file'
    argv, options = calls[0]
    assert argv[0] == '/usr/bin/git' and '--no-optional-locks' in argv
    assert '--porcelain=v1' in argv and '-z' in argv and '--ignore-submodules=all' in argv
    assert f'--git-dir={sample.root}/.git' in argv
    assert options['env']['GIT_CONFIG_GLOBAL'] == '/dev/null'
    assert options['env']['GIT_OPTIONAL_LOCKS'] == '0'
    assert options['stdin'] == subprocess.DEVNULL and options['start_new_session'] is True
    assert 'shell' not in options


def test_parser_failure_remains_failure(sample, monkeypatch):
    monkeypatch.setattr(capture, '_capture', lambda *args, **kwargs: b'?? incomplete')
    with pytest.raises(GitStatusParseError):
        capture.collect_sample_git_status(sample)


def test_spawn_failure_is_safe(sample, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('PRIVATE')
    monkeypatch.setattr(capture.subprocess, 'Popen', fail)
    with pytest.raises(capture.GitStatusCaptureError) as caught:
        capture.collect_sample_git_status(sample)
    assert caught.value.code == 'git_status_unavailable' and 'PRIVATE' not in str(caught.value)


@pytest.mark.parametrize('failure', [KeyboardInterrupt, OSError])
def test_read_interruption_always_reaps_process(sample, monkeypatch, failure):
    original_spawn = subprocess.Popen
    original_selector = capture.selectors.DefaultSelector
    children = []

    def spawn(argv, **kwargs):
        process = original_spawn((sys.executable, '-c', 'import time; time.sleep(30)'), **kwargs)
        children.append(process)
        return process

    class FailingSelector(original_selector):
        def select(self, timeout=None):
            raise failure('PRIVATE')

    monkeypatch.setattr(capture.subprocess, 'Popen', spawn)
    monkeypatch.setattr(capture.selectors, 'DefaultSelector', FailingSelector)
    expected = KeyboardInterrupt if failure is KeyboardInterrupt else capture.GitStatusCaptureError
    with pytest.raises(expected):
        capture.collect_sample_git_status(sample)
    assert children[0].poll() is not None
    assert children[0].stdout.closed and children[0].stderr.closed
