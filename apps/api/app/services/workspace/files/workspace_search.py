"""在单个受限文本文件中进行字面量搜索，不执行正则或外部命令。"""

from dataclasses import dataclass

from app.services.workspace.files.workspace_file import read_task_text_file


# 查询和输出上限由服务端决定，暂不允许调用方扩大。
MAX_SEARCH_QUERY_CHARACTERS = 128
MAX_SEARCH_MATCH_LINES = 50
MAX_SEARCH_SNIPPET_CHARACTERS = 200
SEARCH_PREFIX_CHARACTERS = 40


class WorkspaceSearchError(ValueError):
    """查询格式错误不包含文件内容或本机路径。"""

    code = "invalid_search_query"

    def __init__(self) -> None:
        super().__init__(
            "查询须为 1～128 个字符，且不能包含换行或 NUL 字符"
        )


@dataclass(frozen=True)
class WorkspaceSearchMatch:
    """一个匹配行的首次命中位置及有限片段。"""

    # 行号和列号从 1 开始；列号按 Python 字符计数。
    line_number: int
    column_number: int

    # 片段不包含行结束符，且保留原始大小写与空格。
    snippet: str
    snippet_start_column: int
    snippet_truncated: bool


@dataclass(frozen=True)
class WorkspaceSearchResult:
    """只返回有限匹配行，不声称提供总匹配次数或完整文件快照。"""

    relative_path: str
    query: str
    matches: tuple[WorkspaceSearchMatch, ...]
    truncated: bool


def _validate_query(query: str) -> None:
    """只做查询格式校验，不访问数据库或文件系统。"""

    # 不 strip：空格本身可以是用户要搜索的内容。
    # 空查询会命中所有位置，因此必须明确拒绝。
    if (
        not isinstance(query, str)
        or not 1 <= len(query) <= MAX_SEARCH_QUERY_CHARACTERS
        or any(character in query for character in ("\r", "\n", "\x00"))
    ):
        raise WorkspaceSearchError()


def _search_content(
    content: str,
    query: str,
) -> tuple[tuple[WorkspaceSearchMatch, ...], bool]:
    """搜索已读取的有限文本；调用方负责先验证查询和文件边界。"""

    matches: list[WorkspaceSearchMatch] = []
    truncated = False

    # 明确只将 CRLF、CR、LF 视为行边界。
    # 不修改行内空格、大小写或其他 Unicode 字符。
    normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")

    for line_number, line in enumerate(normalized_content.split("\n"), start=1):
        # str.find 是字面量匹配；不解释正则、通配符或命令。
        # 每行只记录第一次出现，避免同一行重复占用结果预算。
        position = line.find(query)
        if position < 0:
            continue

        # 多发现一个匹配行，才能确认确实有未返回的结果。
        # 不继续统计总匹配数，也不扫描后面的行来做排序。
        if len(matches) == MAX_SEARCH_MATCH_LINES:
            truncated = True
            break

        # 保留最多 40 个前置字符。
        # 查询最长 128 个字符，40 + 128 <= 200，
        # 因此返回片段一定包含完整的首次匹配。
        snippet_start = max(0, position - SEARCH_PREFIX_CHARACTERS)
        snippet_end = min(
            len(line),
            snippet_start + MAX_SEARCH_SNIPPET_CHARACTERS,
        )

        matches.append(
            WorkspaceSearchMatch(
                line_number=line_number,
                column_number=position + 1,
                snippet=line[snippet_start:snippet_end],
                snippet_start_column=snippet_start + 1,
                snippet_truncated=(
                    snippet_start > 0 or snippet_end < len(line)
                ),
            )
        )

    return tuple(matches), truncated


def search_task_text_file(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    relative_path: str,
    query: str,
) -> WorkspaceSearchResult:
    """授权读取单个文件后搜索，身份与任务定位必须来自服务端。"""

    # 查询语法不依赖资源信息；非法输入在任何 I/O 前拒绝。
    _validate_query(query)

    # 复用现有归属、目录、路径、普通文件、大小和 UTF-8 检查。
    # 不重新实现文件打开，也不将读取失败转换成“没有匹配”。
    source = read_task_text_file(
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        relative_path=relative_path,
    )

    # 读取服务返回时 Session 和文件描述符均已关闭。
    # 搜索只处理这次读到的内存文本，不跨搜索过程持有资源。
    matches, truncated = _search_content(
        content=source.content,
        query=query,
    )

    return WorkspaceSearchResult(
        relative_path=source.relative_path,
        query=query,
        matches=matches,
        truncated=truncated,
    )
