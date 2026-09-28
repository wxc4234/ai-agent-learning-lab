"""可信临时Git样例的有界只读diff采集；不解析或应用补丁。"""

from dataclasses import dataclass
from typing import Literal

from app.services.workspace.git import status_capture as capture


GitDiffScope = Literal['worktree', 'staged']

# 使用显式映射，既保留失败原因，也避免传播底层异常和私有路径。
_CAPTURE_ERROR_CODES = {
    'git_sample_unavailable': 'git_sample_unavailable',
    'git_status_timeout': 'git_diff_timeout',
    'git_status_output_limit': 'git_diff_output_limit',
    'git_status_command_failed': 'git_diff_command_failed',
    'git_status_unavailable': 'git_diff_unavailable',
    'git_status_platform_unsupported': 'git_diff_platform_unsupported',
}


class GitDiffCaptureError(ValueError):
    """固定安全错误，不包含stderr、宿主路径或命令环境。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__('Git差异采集未完成：' + code)


@dataclass(frozen=True)
class GitDiffSnapshot:
    """一次成功采集的原始结果，不代表整个仓库干净或补丁可应用。"""

    # 范围随结果返回，避免调用方混淆暂存与未暂存差异。
    scope: GitDiffScope

    # 保留原始字节，不因解码替换而悄悄改变文件内容。
    data: bytes


def collect_sample_git_diff(
    sample: capture.GitStatusSample,
    *,
    scope: GitDiffScope,
) -> GitDiffSnapshot:
    """仅允许固定比较范围；不接受宿主路径、revision或额外Git参数。"""

    # 类型注解不提供运行时校验，内部调用也必须经过白名单检查。
    if type(scope) is not str or scope not in ('worktree', 'staged'):
        raise GitDiffCaptureError('git_diff_invalid_scope')

    try:
        source = capture._require_sample_source(sample)
        root = sample.root
        metadata = root / '.git'

        # staged明确要求HEAD基线；缺少基线时保留命令失败，
        # 不自动创建提交，也不悄悄换成另一种比较语义。
        comparison = ('--cached', 'HEAD') if scope == 'staged' else ()

        # 仓库位置来自已登记句柄，命令和配置全部由服务端固定。
        argv = (
            capture.GIT_EXECUTABLE,
            '--no-optional-locks',
            '--no-pager',
            f'--git-dir={metadata}',
            f'--work-tree={root}',
            '-c', 'core.fsmonitor=false',
            '-c', 'core.untrackedCache=false',
            '-c', 'core.hooksPath=/dev/null',
            '-c', 'core.excludesFile=/dev/null',
            '-c', 'core.attributesFile=/dev/null',
            '-c', 'core.quotePath=true',
            'diff',
            '--patch',
            '--no-color',
            '--no-ext-diff',
            '--no-textconv',
            '--no-renames',
            '--ignore-submodules=all',
            '--diff-algorithm=myers',
            '--no-indent-heuristic',
            '--unified=3',
            '--src-prefix=a/',
            '--dst-prefix=b/',
            *comparison,
            '--',
        )

        # 复用既有双管道预算、超时和进程回收。
        # 只有完整读取且退出码为0，_capture才会返回bytes。
        data = capture._capture(
            argv,
            cwd=root,
            env=capture._environment(source.directory),
        )
    except capture.GitStatusCaptureError as exc:
        code = _CAPTURE_ERROR_CODES.get(
            exc.code,
            'git_diff_unavailable',
        )
        raise GitDiffCaptureError(code) from None

    return GitDiffSnapshot(scope=scope, data=data)
