"""纯解析 porcelain v1 -z；不执行命令、不解析文件系统授权。

协议依据：https://git-scm.com/docs/git-status#_porcelain_format_version_1
仅接收无分支头的完整 stdout。调用者还必须确认命令成功且收集完成；
空 stdout 不能单独证明命令成功、仓库存在或整个仓库干净。
"""

from dataclasses import dataclass
from typing import Literal

MAX_STATUS_BYTES = 256 * 1024
MAX_STATUS_ENTRIES = 2000
MAX_STATUS_PATH_BYTES = 4096

# 合并冲突的 XY 不是暂存区/工作区状态，必须先于普通状态分类。
_UNMERGED = frozenset({'DD', 'AU', 'UD', 'UA', 'DU', 'AA', 'UU'})
_TRACKED = frozenset(
    {' A', ' M', ' T', ' D', ' R', ' C', 'D '}
    | {x + y for x in 'MTARC' for y in ' MTD'}
)
_ERRORS = {
    'git_status_invalid_input': 'Git状态解析输入类型不符合要求',
    'git_status_truncated': 'Git状态输出被截断，无法确认完整状态',
    'git_status_limit_exceeded': 'Git状态数据超过解析预算',
    'git_status_invalid_format': 'Git状态输出不符合porcelain v1 -z协议',
    'git_status_invalid_encoding': 'Git状态路径不是有效UTF-8文本',
}


class GitStatusParseError(ValueError):
    """固定安全错误；不附带可能包含私有文件名的原始输入。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(_ERRORS[code])


@dataclass(frozen=True)
class GitStatusEntry:
    """路径原样保留，仅为展示数据，不赋予后续读取或写入权限。"""

    xy: str
    kind: Literal['tracked', 'unmerged', 'untracked', 'ignored']
    path: str
    original_path: str | None = None

    @property
    def index_status(self) -> str | None:
        return self.xy[0] if self.kind == 'tracked' else None

    @property
    def worktree_status(self) -> str | None:
        return self.xy[1] if self.kind == 'tracked' else None


@dataclass(frozen=True)
class GitStatusSnapshot:
    """所有记录验证通过才构造结果；空tuple仅表示此次输出没有记录。"""

    entries: tuple[GitStatusEntry, ...]
    byte_count: int


def _path(raw: bytes) -> str:
    if not raw:
        raise GitStatusParseError('git_status_invalid_format')
    if len(raw) > MAX_STATUS_PATH_BYTES:
        raise GitStatusParseError('git_status_limit_exceeded')
    try:
        # 严格拒绝非法字节，不用替换字符改变目标身份；不strip/unescape。
        return raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        raise GitStatusParseError('git_status_invalid_encoding') from None


def parse_git_status(data: bytes, *, source_truncated: bool) -> GitStatusSnapshot:
    """预算超限或输入截断均失败，不返回看似完整的部分条目。"""

    if type(data) is not bytes or type(source_truncated) is not bool:
        raise GitStatusParseError('git_status_invalid_input')
    if source_truncated:
        raise GitStatusParseError('git_status_truncated')
    if len(data) > MAX_STATUS_BYTES:
        raise GitStatusParseError('git_status_limit_exceeded')
    if not data:
        return GitStatusSnapshot((), 0)
    if not data.endswith(b'\0'):
        raise GitStatusParseError('git_status_invalid_format')

    # 总字节预算先于split，防止无界分配；终止NUL只移除一个，空记录仍拒绝。
    fields = data[:-1].split(b'\0')
    entries = []
    cursor = 0
    while cursor < len(fields):
        if len(entries) >= MAX_STATUS_ENTRIES:
            raise GitStatusParseError('git_status_limit_exceeded')
        record = fields[cursor]
        cursor += 1
        if len(record) < 4 or record[2:3] != b' ':
            raise GitStatusParseError('git_status_invalid_format')
        try:
            xy = record[:2].decode('ascii')
        except UnicodeDecodeError:
            raise GitStatusParseError('git_status_invalid_format') from None
        if xy in _UNMERGED:
            kind = 'unmerged'
        elif xy == '??':
            kind = 'untracked'
        elif xy == '!!':
            kind = 'ignored'
        elif xy in _TRACKED:
            kind = 'tracked'
        else:
            raise GitStatusParseError('git_status_invalid_format')
        path = _path(record[3:])
        original = None
        if 'R' in xy or 'C' in xy:
            # -z移除箭头，并反转常见短格式的路径顺序：to NUL from NUL。
            if cursor >= len(fields):
                raise GitStatusParseError('git_status_invalid_format')
            original = _path(fields[cursor])
            cursor += 1
        entries.append(GitStatusEntry(xy, kind, path, original))
    return GitStatusSnapshot(tuple(entries), len(data))
