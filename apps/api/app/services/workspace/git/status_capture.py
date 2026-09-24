"""POSIX自建样例的有界Git状态采集；不是普通项目或不可信仓库沙箱。"""

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import tempfile
from time import monotonic

from app.services.runtime.command.command_environment import build_posix_command_environment
from app.services.workspace.git.status_parser import MAX_STATUS_BYTES, GitStatusSnapshot, parse_git_status

GIT_EXECUTABLE = '/usr/bin/git'
STATUS_TIMEOUT_SECONDS = 5.0
MAX_STDERR_BYTES = 16 * 1024


class GitStatusCaptureError(ValueError):
    """不传播stderr、命令路径、环境或底层异常。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__('Git状态采集未完成：' + code)


@dataclass(frozen=True, slots=True, eq=False)
class GitStatusSample:
    """仅可信夹具可读取root准备样例，不能从模型参数反序列化。"""

    root: Path


@dataclass(frozen=True)
class _Source:
    directory: Path
    root_identity: tuple[int, int]
    git_identity: tuple[int, int]
    config: bytes


_SAMPLES: dict[GitStatusSample, _Source] = {}


def _identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise GitStatusCaptureError('git_sample_unavailable')
    return info.st_dev, info.st_ino


def _environment(directory: Path) -> dict[str, str]:
    env = build_posix_command_environment(home_directory=str(directory), temporary_directory=str(directory))
    # 不继承宿主GIT_DIR/INDEX/CONFIG/PATH；禁用全局配置、提示与可选索引刷新。
    env.update(GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null',
               GIT_CONFIG_NOSYSTEM='1', GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0',
               GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1')
    return env


def _capture(argv: tuple[str, ...], *, cwd: Path, env: dict[str, str]) -> bytes:
    """两路原始字节独立预算，完成退出与EOF后才交给解析器。"""

    process = None
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        deadline = monotonic() + STATUS_TIMEOUT_SECONDS
        buffers = [bytearray(), bytearray()]
        limits = [MAX_STATUS_BYTES, MAX_STDERR_BYTES]
        with selectors.DefaultSelector() as selector:
            for index, stream in enumerate((process.stdout, process.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, index)
            while selector.get_map():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise GitStatusCaptureError('git_status_timeout')
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    index = key.data
                    if len(buffers[index]) + len(chunk) > limits[index]:
                        raise GitStatusCaptureError('git_status_output_limit')
                    buffers[index].extend(chunk)
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise GitStatusCaptureError('git_status_timeout')
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise GitStatusCaptureError('git_status_timeout') from None
        if code != 0:
            raise GitStatusCaptureError('git_status_command_failed')
        return bytes(buffers[0])
    except OSError:
        raise GitStatusCaptureError('git_status_unavailable') from None
    finally:
        if process is not None:
            # 异常/中断不能遗留子进程；本样例只启动固定Git，不提供任意命令接口。
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


@contextmanager
def temporary_git_status_sample():
    """创建并拥有整个临时目录；退出撤销句柄并清理，不登记用户项目。"""

    if os.name != 'posix':
        raise GitStatusCaptureError('git_status_platform_unsupported')
    with tempfile.TemporaryDirectory(prefix='agent-git-status-') as directory:
        parent = Path(directory).resolve()
        root = parent / 'repository'
        root.mkdir()
        # 初始化只发生在新建空目录；空模板防止系统模板带入hooks或配置。
        _capture((GIT_EXECUTABLE, 'init', '--quiet', '--template=', str(root)), cwd=parent, env=_environment(parent))
        sample = GitStatusSample(root)
        _SAMPLES[sample] = _Source(parent, _identity(root), _identity(root / '.git'), (root / '.git/config').read_bytes())
        try:
            yield sample
        finally:
            _SAMPLES.pop(sample, None)


def collect_sample_git_status(sample: GitStatusSample) -> GitStatusSnapshot:
    """仅本进程登记对象可调用；禁止外部并发写者，不提供Workspace授权。"""

    if type(sample) is not GitStatusSample or sample not in _SAMPLES:
        raise GitStatusCaptureError('git_sample_unavailable')
    source = _SAMPLES[sample]
    root = sample.root
    metadata = root / '.git'
    try:
        if _identity(root) != source.root_identity or _identity(metadata) != source.git_identity:
            raise GitStatusCaptureError('git_sample_unavailable')
        config = metadata / 'config'
        if (not stat.S_ISREG(config.lstat().st_mode) or config.stat().st_size != len(source.config)
                or config.read_bytes() != source.config
                or any((metadata / name).exists() or (metadata / name).is_symlink()
                       for name in ('commondir', 'config.worktree', 'objects/info/alternates'))):
            raise GitStatusCaptureError('git_sample_unavailable')
    except OSError:
        raise GitStatusCaptureError('git_sample_unavailable') from None
    # 显式git-dir/work-tree，不向父目录发现仓库；配置冻结且参数不能由模型指定。
    argv = (GIT_EXECUTABLE, '--no-optional-locks', '--no-pager',
            f'--git-dir={metadata}', f'--work-tree={root}',
            '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
            '-c', 'core.hooksPath=/dev/null', '-c', 'core.excludesFile=/dev/null',
            '-c', 'core.attributesFile=/dev/null', '-c', 'core.quotePath=false',
            'status', '--porcelain=v1', '-z', '--untracked-files=all',
            '--ignore-submodules=all', '--renames')
    data = _capture(argv, cwd=root, env=_environment(source.directory))
    return parse_git_status(data, source_truncated=False)
