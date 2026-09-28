"""以可类型检查的方式读取平台专用的系统能力。"""

import asyncio
import errno
import os
import signal
import socket
from collections.abc import Awaitable, Callable
from typing import cast


def _open_flag(name: str) -> int:
    """不支持的平台返回零；公开入口仍必须先执行能力检查。"""

    value = getattr(os, name, 0)
    return value if isinstance(value, int) else 0


O_DIRECTORY = _open_flag("O_DIRECTORY")
O_NOFOLLOW = _open_flag("O_NOFOLLOW")
O_NONBLOCK = _open_flag("O_NONBLOCK")

_GET_EFFECTIVE_USER_ID = cast(
    Callable[[], int] | None,
    getattr(os, "geteuid", None),
)
HAS_EFFECTIVE_USER_ID = _GET_EFFECTIVE_USER_ID is not None

_FCHMOD = cast(
    Callable[[int, int], None] | None,
    getattr(os, "fchmod", None),
)
_FCHOWN = cast(
    Callable[[int, int, int], None] | None,
    getattr(os, "fchown", None),
)
_KILL_PROCESS_GROUP = cast(
    Callable[[int, int], None] | None,
    getattr(os, "killpg", None),
)
_SIGKILL = getattr(signal, "SIGKILL", None)
_AF_UNIX = getattr(socket, "AF_UNIX", None)
_OPEN_UNIX_CONNECTION = cast(
    Callable[..., Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]] | None,
    getattr(asyncio, "open_unix_connection", None),
)


def get_effective_user_id() -> int:
    """返回有效用户 ID；Windows 等不支持的平台明确拒绝。"""

    if _GET_EFFECTIVE_USER_ID is None:
        raise OSError(errno.ENOSYS, "effective user ID is unavailable")
    return _GET_EFFECTIVE_USER_ID()


def get_stat_flags(metadata: os.stat_result) -> int:
    """读取 BSD/macOS 文件标志；不支持时不伪造零值。"""

    value = getattr(metadata, "st_flags", None)
    if not isinstance(value, int):
        raise OSError(errno.ENOSYS, "file flags are unavailable")
    return value


def set_file_mode(descriptor: int, mode: int) -> None:
    if _FCHMOD is None:
        raise OSError(errno.ENOSYS, "descriptor chmod is unavailable")
    _FCHMOD(descriptor, mode)


def set_file_owner(descriptor: int, user_id: int, group_id: int) -> None:
    if _FCHOWN is None:
        raise OSError(errno.ENOSYS, "descriptor chown is unavailable")
    _FCHOWN(descriptor, user_id, group_id)


def kill_process_group(process_id: int) -> None:
    """强制终止 POSIX 进程组；Windows 明确报告能力缺失。"""

    if _KILL_PROCESS_GROUP is None or not isinstance(_SIGKILL, int):
        raise OSError(errno.ENOSYS, "process groups are unavailable")
    _KILL_PROCESS_GROUP(process_id, _SIGKILL)


def create_unix_stream_socket() -> socket.socket:
    if not isinstance(_AF_UNIX, int):
        raise OSError(errno.ENOSYS, "Unix sockets are unavailable")
    return socket.socket(_AF_UNIX, socket.SOCK_STREAM)


async def open_unix_stream_connection(
    *, sock: socket.socket, limit: int,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if _OPEN_UNIX_CONNECTION is None:
        raise OSError(errno.ENOSYS, "Unix stream connections are unavailable")
    return await _OPEN_UNIX_CONNECTION(sock=sock, limit=limit)
