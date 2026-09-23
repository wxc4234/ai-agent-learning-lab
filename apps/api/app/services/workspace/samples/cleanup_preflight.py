"""清理待办的内部只读预检；候选路径绝不构成文件清理授权。"""

import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


PreflightResult = Literal[
    'evidence_missing',
    'not_pending',
    'evidence_inconsistent',
    'directory_missing',
    'identity_unverifiable',
    'identity_matches_record',
    'inspection_unavailable',
]


@dataclass(frozen=True)
class TaskSampleCleanupPreflight:
    """只含固定诊断分类；不携带来源路径、句柄或清理能力。"""

    result: PreflightResult


def inspect_pending_sample_directory(
    root_path: str,
    *,
    expected_parent_identity: tuple[int, int] | None = None,
    expected_root_identity: tuple[int, int] | None = None,
) -> PreflightResult:
    """只读观察当前候选；身份数值匹配不是清理或执行授权。"""

    # 历史记录须两个身份都未知；新记录须两个身份都完整。
    if (expected_parent_identity is None) != (expected_root_identity is None):
        return 'evidence_inconsistent'
    for identity in (expected_parent_identity, expected_root_identity):
        if identity is not None and (
            type(identity) is not tuple
            or len(identity) != 2
            or type(identity[0]) is not int
            or type(identity[1]) is not int
            or identity[0] < 0
            or identity[1] <= 0
        ):
            return 'evidence_inconsistent'

    if sys.platform != 'darwin':
        return 'inspection_unavailable'
    try:
        parent = Path(tempfile.gettempdir()).resolve(strict=True)
    except (OSError, RuntimeError):
        return 'inspection_unavailable'

    # 数据库路径不是授权；先限定为服务端生成的规范名字。
    if type(root_path) is not str:
        return 'evidence_inconsistent'
    path = Path(root_path)
    name = path.name
    if (
        not path.is_absolute()
        or '..' in path.parts
        or str(path) != root_path
        or re.fullmatch(r'agent-proposal-[0-9a-f]{32}', name) is None
    ):
        return 'evidence_inconsistent'
    if path.parent != parent:
        # TMPDIR 可能在重启后改变；不探查任意旧父目录。
        return 'inspection_unavailable'

    try:
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return 'inspection_unavailable'

    result: PreflightResult = 'inspection_unavailable'
    candidate_fd: int | None = None
    try:
        def observed_parent_identity() -> tuple[int, int] | None:
            opened = os.fstat(parent_fd)
            linked = os.stat(parent, follow_symlinks=False)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or not stat.S_ISDIR(linked.st_mode)
                or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)
            ):
                return None
            return opened.st_dev, opened.st_ino

        parent_identity = observed_parent_identity()
        if parent_identity is not None:
            if (
                expected_parent_identity is not None
                and parent_identity != expected_parent_identity
            ):
                result = 'evidence_inconsistent'
            else:
                try:
                    candidate = os.stat(
                        name,
                        dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    result = 'directory_missing'
                except OSError:
                    result = 'inspection_unavailable'
                else:
                    # 目录项形态明显不符时不尝试打开候选对象。
                    valid_candidate = (
                        stat.S_ISDIR(candidate.st_mode)
                        and candidate.st_uid == os.geteuid()
                        and stat.S_IMODE(candidate.st_mode) == 0o700
                    )
                    if not valid_candidate:
                        result = 'evidence_inconsistent'
                    else:
                        try:
                            # 只读打开当前目录项，不沿链接追踪，也不读取目录内容。
                            candidate_fd = os.open(
                                name,
                                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=parent_fd,
                            )
                            opened_candidate = os.fstat(candidate_fd)
                            linked_again = os.stat(
                                name,
                                dir_fd=parent_fd,
                                follow_symlinks=False,
                            )
                        except OSError:
                            # 已看到目录后再消失或无法打开，是不稳定观察而非初次缺失。
                            result = 'inspection_unavailable'
                        else:
                            same_directory = (
                                stat.S_ISDIR(opened_candidate.st_mode)
                                and stat.S_ISDIR(linked_again.st_mode)
                                and opened_candidate.st_uid == candidate.st_uid
                                and linked_again.st_uid == candidate.st_uid
                                and stat.S_IMODE(opened_candidate.st_mode) == 0o700
                                and stat.S_IMODE(linked_again.st_mode) == 0o700
                                and (candidate.st_dev, candidate.st_ino)
                                == (opened_candidate.st_dev, opened_candidate.st_ino)
                                == (linked_again.st_dev, linked_again.st_ino)
                            )
                            if not same_directory:
                                result = 'inspection_unavailable'
                            elif expected_root_identity is None:
                                result = 'identity_unverifiable'
                            elif (opened_candidate.st_dev, opened_candidate.st_ino) == expected_root_identity:
                                # 只说明本次三个只读观察与记录数值一致，不是CAS或清理权。
                                result = 'identity_matches_record'
                            else:
                                result = 'evidence_inconsistent'

            # 父目录在观察期间变化时，不保留刚才取得的候选结论。
            if observed_parent_identity() != parent_identity:
                result = 'inspection_unavailable'
    except OSError:
        result = 'inspection_unavailable'
    finally:
        # 候选先于父目录关闭；关闭失败也不能保留匹配结论。
        if candidate_fd is not None:
            try:
                os.close(candidate_fd)
            except OSError:
                result = 'inspection_unavailable'
        try:
            os.close(parent_fd)
        except OSError:
            result = 'inspection_unavailable'
    return result
