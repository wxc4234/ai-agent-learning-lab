"""授权 Workspace 内的 Markdown 清单与可核验来源，不修改笔记。"""

from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath, PureWindowsPath

from app.services.workspace.files.workspace_file import read_task_text_file
from app.services.workspace.files.workspace_listing import list_task_directory


# 预算由服务端固定，调用者不能通过请求扩大范围。
MAX_VAULT_DIRECTORIES = 20
MAX_VAULT_DEPTH = 8
MAX_VAULT_FILES = 100
MAX_VAULT_PATH_CHARACTERS = 4096


class VaultError(ValueError):
    """只使用固定错误码和文案，不包含宿主路径或笔记正文。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class VaultListing:
    """清单中的路径均相对于当前 Workspace 绑定根目录。"""

    workspace_id: str
    task_id: str
    paths: tuple[str, ...]
    scanned_directories: int

    # True 表示覆盖不完整，不能把空列表解释成 Vault 没有 Markdown。
    truncated: bool


@dataclass(frozen=True)
class VaultSource:
    """标识本次读取来源；摘要用于核对内容，不授予后续访问权。"""

    workspace_id: str
    relative_path: str
    sha256: str
    start_line: int | None
    end_line: int | None


@dataclass(frozen=True)
class VaultDocument:
    """正文与来源由同一次受限读取构造，避免拼接不一致的结果。"""

    task_id: str
    source: VaultSource
    content: str
    byte_count: int


def _vault_path(raw_path: str) -> PurePosixPath:
    """先检查输入语法与 Vault 范围，不访问数据库或文件系统。"""

    if (
        not isinstance(raw_path, str)
        or not 1 <= len(raw_path) <= MAX_VAULT_PATH_CHARACTERS
        or "\x00" in raw_path
        or "\\" in raw_path
    ):
        raise VaultError(
            "invalid_vault_path",
            "请提供有效的 Vault 内相对路径",
        )

    path = PurePosixPath(raw_path)
    windows_path = PureWindowsPath(raw_path)

    if (
        path.is_absolute()
        or windows_path.drive
        or windows_path.root
        or ".." in path.parts
    ):
        raise VaultError(
            "invalid_vault_path",
            "Vault 路径不能使用绝对路径、盘符或上级目录",
        )

    # 与现有路径协议保持一致；保留合法文件名的原始空格和大小写。
    invalid_characters = '<>:"|?*'
    for part in path.parts:
        if (
            any(
                character in invalid_characters or ord(character) < 32
                for character in part
            )
            or part.endswith((" ", "."))
            or PureWindowsPath(part).is_reserved()
        ):
            raise VaultError(
                "invalid_vault_path",
                "路径包含当前入口不支持的名称",
            )

        # 初版统一排除隐藏项，包括 .obsidian、.git 和 .trash。
        if part.startswith("."):
            raise VaultError(
                "vault_path_excluded",
                "Vault 入口不访问隐藏文件或隐藏目录",
            )

    return path


def list_vault_markdown(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
) -> VaultListing:
    """按目录广度优先枚举；只返回普通 Markdown 文件的相对路径。"""

    pending: deque[tuple[PurePosixPath, int]] = deque(
        [(PurePosixPath("."), 0)]
    )
    paths: list[str] = []
    scanned_directories = 0
    truncated = False
    stopped = False

    while pending and not stopped:
        directory, depth = pending.popleft()

        # 每层调用都重新授权，并拒绝目录路径上的符号链接。
        # 底层只读事务在返回目录条目前已经结束。
        listing = list_task_directory(
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
            relative_path=directory.as_posix(),
            require_direct_path=True,
        )
        scanned_directories += 1
        truncated = truncated or listing.truncated

        for entry in listing.entries:
            # 隐藏项和链接属于明确排除的范围，不打开其内容或目标。
            if entry.name.startswith("."):
                continue
            if entry.kind not in {"file", "directory"}:
                continue

            child = directory / entry.name
            try:
                child = _vault_path(child.as_posix())
            except VaultError:
                # 存在无法表示的普通条目，不能声称扫描完整。
                truncated = True
                continue

            if entry.kind == "file":
                if child.suffix.lower() != ".md":
                    continue

                # 多发现一个匹配，才能确认结果数量发生截断。
                if len(paths) == MAX_VAULT_FILES:
                    truncated = True
                    stopped = True
                    break

                paths.append(child.as_posix())
                continue

            # 已扫描目录和待扫描目录共用一个全局预算。
            # 队列不会先增长到任意大小，再在扫描时才判断上限。
            if (
                depth >= MAX_VAULT_DEPTH
                or scanned_directories + len(pending)
                >= MAX_VAULT_DIRECTORIES
            ):
                truncated = True
                continue

            pending.append((child, depth + 1))

    # 只排序取得的有限结果，不为排序扫描整个 Vault。
    paths.sort()

    return VaultListing(
        workspace_id=workspace_id,
        task_id=task_id,
        paths=tuple(paths),
        scanned_directories=scanned_directories,
        truncated=truncated,
    )


def read_vault_markdown(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    relative_path: str,
) -> VaultDocument:
    """重新授权并读取 Markdown，不把先前清单当成访问许可。"""

    path = _vault_path(relative_path)

    if path.suffix.lower() != ".md":
        raise VaultError(
            "vault_file_unsupported",
            "Vault 入口只支持 .md 文件",
        )

    text_file = read_task_text_file(
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        relative_path=path.as_posix(),
        require_direct_path=True,
    )

    # 底层严格解码 UTF-8，重新编码可还原本次读到的字节。
    # 不再次打开文件，避免摘要与正文来自两次不同读取。
    content_bytes = text_file.content.encode("utf-8")
    line_count = len(text_file.content.splitlines())

    return VaultDocument(
        task_id=task_id,
        source=VaultSource(
            workspace_id=workspace_id,
            relative_path=text_file.relative_path,
            sha256=sha256(content_bytes).hexdigest(),
            start_line=1 if line_count else None,
            end_line=line_count if line_count else None,
        ),
        content=text_file.content,
        byte_count=text_file.byte_count,
    )
