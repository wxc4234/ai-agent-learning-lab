"""授权 Vault 的有界跨文件字面检索，不修改笔记或调用模型。"""

from dataclasses import dataclass, replace
from typing import Literal

from app.services.workspace.files.vault import (
    VaultSource,
    list_vault_markdown,
    read_vault_markdown,
)
from app.services.workspace.files.workspace_search import (
    search_text_content,
    validate_search_query,
)


# 限制由服务端决定，客户端不能扩大读取量或返回量。
MAX_VAULT_SEARCH_FILES = 20
MAX_VAULT_SEARCH_MATCHES = 50

VaultSearchIncompleteReason = Literal[
    "inventory_truncated",
    "file_budget",
    "match_budget",
]


@dataclass(frozen=True)
class VaultSearchMatch:
    """一个命中行的首次匹配及可核验来源。"""

    # source 的行号范围指向命中行，SHA-256 仍对应本次读取的完整文件。
    source: VaultSource

    # 列号从 1 开始，按 Python Unicode 字符计数，不按 UTF-16 计数。
    column_number: int

    # 片段保留原始大小写和空格，不包含行结束符。
    snippet: str
    snippet_start_column: int
    snippet_truncated: bool


@dataclass(frozen=True)
class VaultSearchResult:
    """有限检索结果；空匹配必须结合覆盖状态解释。"""

    workspace_id: str
    task_id: str
    query: str
    matches: tuple[VaultSearchMatch, ...]

    # 记录成功读取并搜索的文件数，以及这些完整文件的原始字节数。
    searched_files: int
    read_bytes: int

    # 表示文件覆盖或匹配结果不完整，不表示一定还有更多匹配。
    truncated: bool
    incomplete_reasons: tuple[VaultSearchIncompleteReason, ...]


def search_vault_markdown(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    query: str,
) -> VaultSearchResult:
    """取得授权清单后逐个受限读取，返回有限匹配和覆盖状态。"""

    # 查询格式与资源无关，在数据库或文件系统访问前检查。
    # 不 strip：空格本身可以是用户想查找的内容。
    validate_search_query(query)

    inventory = list_vault_markdown(
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
    )

    matches: list[VaultSearchMatch] = []
    incomplete_reasons: list[VaultSearchIncompleteReason] = []
    searched_files = 0
    read_bytes = 0

    if inventory.truncated:
        incomplete_reasons.append("inventory_truncated")

    # 上一课已经排序取得的有限路径，本课不为排序扫描整个 Vault。
    for relative_path in inventory.paths:
        # 在下一次读取前判断全局文件预算，避免先读取再发现超限。
        if searched_files == MAX_VAULT_SEARCH_FILES:
            incomplete_reasons.append("file_budget")
            break

        # 清单不是访问许可：每个文件都重新检查归属、绑定和路径边界。
        # 读取服务返回时，查询 Session 与文件描述符均已关闭。
        # 不在递归扫描、文件读取或文本匹配期间持有数据库事务。
        document = read_vault_markdown(
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
            relative_path=relative_path,
        )
        searched_files += 1
        read_bytes += document.byte_count

        file_matches, file_truncated = search_text_content(
            content=document.content,
            query=query,
        )

        extra_match_found = False

        for match in file_matches:
            # 多发现一个匹配才确认结果截断。
            # 不把“刚好达到上限”直接解释成还有未返回的匹配。
            if len(matches) == MAX_VAULT_SEARCH_MATCHES:
                extra_match_found = True
                break

            matches.append(
                VaultSearchMatch(
                    source=replace(
                        document.source,
                        start_line=match.line_number,
                        end_line=match.line_number,
                    ),
                    column_number=match.column_number,
                    snippet=match.snippet,
                    snippet_start_column=match.snippet_start_column,
                    snippet_truncated=match.snippet_truncated,
                )
            )

        # 单文件匹配器也有自己的结果预算。
        # 任一层确认存在未返回匹配，本次结果都不能声称完整。
        if extra_match_found or file_truncated:
            incomplete_reasons.append("match_budget")
            break

    return VaultSearchResult(
        workspace_id=workspace_id,
        task_id=task_id,
        query=query,
        matches=tuple(matches),
        searched_files=searched_files,
        read_bytes=read_bytes,
        truncated=bool(incomplete_reasons),
        incomplete_reasons=tuple(incomplete_reasons),
    )
