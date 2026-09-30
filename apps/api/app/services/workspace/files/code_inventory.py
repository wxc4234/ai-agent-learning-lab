"""授权项目的有界源码候选清单，不返回正文、执行代码或写入索引。"""

from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
import re
from typing import Literal, TypedDict

from app.services.workspace.directory.workspace_path import (
    WorkspacePathError,
    _parse_relative_path,
    resolve_task_workspace_path,
)
from app.services.workspace.files.code_ignore import (
    IgnoreRule,
    ignored_by_rules,
    parse_code_ignore,
)
from app.services.workspace.files.workspace_file import (
    WorkspaceFileError,
    read_task_text_file,
)
from app.services.workspace.files.workspace_listing import list_task_directory


# 扫描量由服务端固定；20个候选读取各最多256 KiB，规则读取沿用同一文件上限。
MAX_CODE_DIRECTORIES = 20
MAX_CODE_DEPTH = 8
MAX_CODE_READS = 20
MAX_CODE_PATH_BYTES = 4096

IncompleteReason = Literal[
    "directory_entries",
    "directory_budget",
    "depth_budget",
    "file_budget",
    "unsupported_path",
]
FileType = Literal["source", "configuration"]

# 名称是语言线索，不是语法解析或符号索引的证据。
SOURCE_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "vue",
    ".svelte": "svelte",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".rb": "ruby",
    ".php": "php",
    ".scala": "scala",
    ".lua": "lua",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".dart": "dart",
}
CONFIG_LANGUAGES = {
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".ini": "ini",
    ".cfg": "ini",
    ".xml": "xml",
}
SPECIAL_FILES = {
    "Dockerfile": "dockerfile",
    "Makefile": "make",
    "CMakeLists.txt": "cmake",
}
EXCLUDED_DIRECTORIES = {
    "node_modules",
    "vendor",
    "venv",
    "build",
    "dist",
    "out",
    "coverage",
    "target",
    "__pycache__",
}
GENERATED_FILES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "uv.lock",
    "cargo.lock",
}
SENSITIVE_STEMS = {
    "secret",
    "secrets",
    "credential",
    "credentials",
    "password",
    "passwords",
    "token",
    "tokens",
    "api_keys",
    "service_account",
    "service-account",
}

# 保守启发式仅排除疑似内容；不能宣称检测到了所有密钥或已实现通用脱敏。
SUSPICIOUS_CONTENT = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]{0,32})?PRIVATE KEY-----"
    r"|\bsk-[A-Za-z0-9_-]{16,}"
    r"|\bAKIA[0-9A-Z]{16}"
    r"|\b(?:api[_-]?key|secret(?:[_-]?key)?|token|password|client[_-]?secret|access[_-]?(?:key|token))\b"
    r"[\"']?[ \t]*[:=][ \t]*[\"'][^\"'\r\n]",
    re.IGNORECASE,
)


class CodeInventoryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Scope(TypedDict):
    user_id: int
    workspace_id: str
    task_id: str


class _Access(_Scope):
    # 只由本次服务端扫描构造，不能来自HTTP输入或旧清单。
    expected_bound_root: str
    require_direct_path: bool


@dataclass(frozen=True)
class CodeFile:
    relative_path: str
    file_type: FileType
    language: str
    # 摘要和字节数对应本次受限读取，不代表返回后文件未变化。
    byte_count: int
    sha256: str


@dataclass(frozen=True)
class CodeInventory:
    workspace_id: str
    task_id: str
    files: tuple[CodeFile, ...]
    scanned_directories: int
    inspected_files: int
    excluded_counts: dict[str, int]
    truncated: bool
    incomplete_reasons: tuple[IncompleteReason, ...]
    source: str = "authorized_code_inventory"
    policy: str = "gitignore_subset_v1"


def _classification(path: PurePosixPath) -> tuple[FileType, str] | None:
    if path.name in SPECIAL_FILES:
        return "configuration", SPECIAL_FILES[path.name]
    extension = path.suffix.lower()
    if extension in SOURCE_LANGUAGES:
        return "source", SOURCE_LANGUAGES[extension]
    if extension in CONFIG_LANGUAGES:
        return "configuration", CONFIG_LANGUAGES[extension]
    return None


def _sensitive_name(name: str) -> bool:
    lower = name.lower()
    return PurePosixPath(lower).stem in SENSITIVE_STEMS or lower.startswith(
        ("secrets.", "credentials.", "id_rsa", "id_ed25519")
    )


def scan_code_inventory(
    *, user_id: int, workspace_id: str, task_id: str
) -> CodeInventory:
    """先过滤再受限读取；失败不返回此前取得的部分候选。"""

    return _scan_code_inventory(
        user_id=user_id, workspace_id=workspace_id, task_id=task_id, consume=None
    )


def _scan_code_inventory(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    consume: Callable[[CodeFile, str], None] | None,
) -> CodeInventory:
    """内部只读消费者共用本轮策略和正文；不提供可重放的文件访问许可。"""

    scope: _Scope = {
        "user_id": user_id,
        "workspace_id": workspace_id,
        "task_id": task_id,
    }
    root = resolve_task_workspace_path(
        **scope, relative_path=".", require_direct_path=True
    )
    # 起始根只用于防止本轮混入另一绑定；后续每次操作仍重新授权。
    access: _Access = {
        **scope,
        "expected_bound_root": str(root),
        "require_direct_path": True,
    }
    pending: deque[tuple[PurePosixPath, int, tuple[IgnoreRule, ...]]] = deque(
        [(PurePosixPath("."), 0, ())]
    )
    files: list[CodeFile] = []
    excluded: Counter[str] = Counter()
    reasons: list[IncompleteReason] = []
    observed_ignore: dict[str, str | None] = {}
    directories = inspected = 0
    stopped = False

    def incomplete(reason: IncompleteReason) -> None:
        if reason not in reasons:
            reasons.append(reason)

    def ignore_text(relative_path: str) -> tuple[str, str | None]:
        try:
            document = read_task_text_file(**access, relative_path=relative_path)
        except WorkspacePathError as error:
            if error.code == "workspace_path_not_found":
                return "", None
            raise
        # resolve时存在、打开时消失是变化，不作为确定的缺失规则处理。
        return document.content, sha256(document.content.encode("utf-8")).hexdigest()

    # 下层 Session 在枚举/读取之前关闭；不跨文件I/O持有数据库事务。
    while pending and not stopped:
        directory, depth, inherited = pending.popleft()
        listing = list_task_directory(**access, relative_path=directory.as_posix())
        directories += 1
        if listing.truncated:
            incomplete("directory_entries")
        control = (directory / ".gitignore").as_posix()
        if any(
            entry.name == ".gitignore" and entry.kind != "file"
            for entry in listing.entries
        ):
            raise CodeInventoryError(
                "code_ignore_unavailable",
                "忽略规则不是可读取的普通文件，未返回源码清单",
            )
        # 即使有限枚举漏掉 .gitignore，也必须独立读取；不能把未知规则当作没有规则。
        content, digest = ignore_text(control)
        observed_ignore[control] = digest
        rules = inherited + parse_code_ignore(content, base=directory.parts)

        for entry in listing.entries:
            if entry.name.startswith("."):
                excluded["hidden"] += 1
                continue
            if entry.kind not in {"file", "directory"}:
                excluded["link_or_special"] += 1
                continue
            is_directory = entry.kind == "directory"
            lower = entry.name.lower()
            if (
                is_directory and lower in EXCLUDED_DIRECTORIES
            ) or lower in GENERATED_FILES:
                excluded["dependency_or_generated"] += 1
                continue
            if _sensitive_name(entry.name):
                excluded["sensitive_name"] += 1
                continue
            child = directory / entry.name
            try:
                if len(child.as_posix().encode("utf-8")) > MAX_CODE_PATH_BYTES:
                    raise ValueError()
                _parse_relative_path(child.as_posix())
            except (UnicodeError, ValueError):
                incomplete("unsupported_path")
                continue
            if ignored_by_rules(child.parts, is_directory=is_directory, rules=rules):
                excluded["gitignore"] += 1
                continue
            if is_directory:
                if depth >= MAX_CODE_DEPTH:
                    incomplete("depth_budget")
                elif directories + len(pending) >= MAX_CODE_DIRECTORIES:
                    incomplete("directory_budget")
                else:
                    pending.append((child, depth + 1, rules))
                continue
            classification = _classification(child)
            if classification is None:
                excluded["unsupported_type"] += 1
                continue
            # 对二进制/超限候选的尝试也计预算，不能无限读取失败候选。
            if inspected == MAX_CODE_READS:
                incomplete("file_budget")
                stopped = True
                break
            inspected += 1
            try:
                document = read_task_text_file(**access, relative_path=child.as_posix())
            except WorkspaceFileError as error:
                if error.code not in {"file_not_utf8_text", "file_too_large"}:
                    raise
                excluded["non_text_or_oversized"] += 1
                continue
            if SUSPICIOUS_CONTENT.search(document.content):
                excluded["suspicious_content"] += 1
                continue
            file_type, language = classification
            file = CodeFile(
                relative_path=document.relative_path,
                file_type=file_type,
                language=language,
                byte_count=document.byte_count,
                sha256=sha256(document.content.encode("utf-8")).hexdigest(),
            )
            files.append(file)
            # 仅在授权、无跟随读取和敏感过滤后调用；消费者不能执行源码或外部写入。
            # 使用同次正文避免二次打开引入另一版本；最终规则/归属复核仍在返回前完成。
            if consume is not None:
                consume(file, document.content)

    # 最后复核已观察的规则及缺失状态；发现变化整次拒绝，不暴露旧策略的部分结果。
    # 这不是跨数据库/文件系统的原子快照，无法证明未观察到的短暂变化不存在。
    for control, expected_digest in observed_ignore.items():
        _, digest = ignore_text(control)
        if digest != expected_digest:
            raise CodeInventoryError(
                "code_ignore_changed", "忽略规则已变化，请重新扫描"
            )
    resolve_task_workspace_path(**access, relative_path=".")
    files.sort(key=lambda file: file.relative_path)
    return CodeInventory(
        workspace_id=workspace_id,
        task_id=task_id,
        files=tuple(files),
        scanned_directories=directories,
        inspected_files=inspected,
        excluded_counts=dict(sorted(excluded.items())),
        truncated=bool(reasons),
        incomplete_reasons=tuple(reasons),
    )
