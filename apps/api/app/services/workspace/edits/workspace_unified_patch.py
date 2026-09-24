"""解析单文件统一 Diff，并在内存中严格计算候选内容。"""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from app.services.workspace.edits.workspace_edit_preview import (
    MAX_EDIT_TEXT_BYTES,
    MAX_PREVIEW_LINES,
)


# 补丁可能同时包含旧内容和新内容，预算独立于最终文件大小。
MAX_PATCH_BYTES = 2 * MAX_EDIT_TEXT_BYTES
MAX_PATCH_LINES = 4 * MAX_PREVIEW_LINES
MAX_PATCH_HUNKS = 128
MAX_PATH_BYTES = 1024

# 省略 count 时表示一行；数字长度也有限制，避免转换任意长整数。
HUNK_HEADER = re.compile(
    r"@@ -([0-9]{1,6})(?:,([0-9]{1,6}))?"
    r" \+([0-9]{1,6})(?:,([0-9]{1,6}))?"
    r" @@(?: [^\n]*)?"
)
NO_NEWLINE_MARKER = "\\ No newline at end of file\n"


class UnifiedPatchError(ValueError):
    """只公开固定错误分类，不回显补丁内容或宿主路径。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("补丁未通过校验，未生成修改结果")


@dataclass(frozen=True, slots=True)
class PatchHunk:
    """两侧位置使用零基索引，文本保留末尾换行事实。"""

    old_index: int
    new_index: int
    before_lines: tuple[str, ...]
    after_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TextPatchCandidate:
    """完整候选内容，不表示已授权或已经写入。"""

    relative_path: str
    updated_content: str
    before_byte_count: int
    after_byte_count: int
    hunk_count: int


def _validate_text(value: str, *, max_bytes: int) -> int:
    """只接受有效 UTF-8/LF 文本，不进行换行或空白归一化。"""

    if (
        not isinstance(value, str)
        or "\x00" in value
        or "\r" in value
    ):
        raise UnifiedPatchError("patch_invalid_text")

    # UTF-8 字节数不少于字符数，先拒绝明显超限的输入。
    if len(value) > max_bytes:
        raise UnifiedPatchError("patch_limit_exceeded")

    try:
        byte_count = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise UnifiedPatchError("patch_invalid_text") from None

    if byte_count > max_bytes:
        raise UnifiedPatchError("patch_limit_exceeded")

    return byte_count


def _validate_relative_path(value: str) -> None:
    """仅检查路径语法；不查询文件存在性、符号链接或业务归属。"""

    _validate_text(value, max_bytes=MAX_PATH_BYTES)

    if (
        not value
        or "\\" in value
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).drive
        or PureWindowsPath(value).root
    ):
        raise UnifiedPatchError("patch_invalid_path")

    # 不先规范化路径，避免隐藏空段、当前目录或上级引用。
    for part in value.split("/"):
        if (
            part in ("", ".", "..")
            or part.endswith((" ", "."))
            or PureWindowsPath(part).is_reserved()
            or any(
                character in '<>:"|?*'
                or ord(character) < 32
                or ord(character) == 127
                for character in part
            )
        ):
            raise UnifiedPatchError("patch_invalid_path")


def _split_lf_lines(content: str) -> list[str]:
    """只按 LF 分行，保留每行是否带换行；空文件返回空列表。"""

    if not content:
        return []

    parts = content.split("\n")
    lines = [part + "\n" for part in parts[:-1]]

    if parts[-1]:
        lines.append(parts[-1])

    return lines


def _parse_range(
    start_text: str,
    count_text: str | None,
) -> tuple[int, int]:
    """将统一 Diff 的范围转换为零基索引及行数。"""

    start = int(start_text)
    count = 1 if count_text is None else int(count_text)

    if start > MAX_PREVIEW_LINES or count > MAX_PREVIEW_LINES:
        raise UnifiedPatchError("patch_limit_exceeded")

    if count:
        if start == 0:
            raise UnifiedPatchError("patch_invalid_format")

        return start - 1, count

    # 空范围表示某行之后的间隙：-0,0 在文件开头，-2,0 在第二行后。
    return start, 0


def _parse_hunks(
    *,
    patch: str,
    relative_path: str,
) -> tuple[PatchHunk, ...]:
    """严格解析约定子集；不接受额外文件或无法解释的尾部内容。"""

    _validate_text(patch, max_bytes=MAX_PATCH_BYTES)
    lines = _split_lf_lines(patch)

    if len(lines) > MAX_PATCH_LINES:
        raise UnifiedPatchError("patch_limit_exceeded")

    # 补丁协议行本身必须以 LF 结束。
    # 文件内容无末尾换行，应使用独立的 NO_NEWLINE_MARKER 表达。
    if len(lines) < 3 or not patch.endswith("\n"):
        raise UnifiedPatchError("patch_invalid_format")

    if lines[:2] != [
        f"--- a/{relative_path}\n",
        f"+++ b/{relative_path}\n",
    ]:
        raise UnifiedPatchError("patch_target_mismatch")

    hunks: list[PatchHunk] = []
    cursor = 2

    while cursor < len(lines):
        if len(hunks) >= MAX_PATCH_HUNKS:
            raise UnifiedPatchError("patch_limit_exceeded")

        match = HUNK_HEADER.fullmatch(lines[cursor][:-1])

        if match is None:
            raise UnifiedPatchError("patch_invalid_format")

        old_index, old_count = _parse_range(
            match.group(1),
            match.group(2),
        )
        new_index, new_count = _parse_range(
            match.group(3),
            match.group(4),
        )

        if old_count == 0 and new_count == 0:
            raise UnifiedPatchError("patch_invalid_format")

        cursor += 1
        before: list[str] = []
        after: list[str] = []

        while cursor < len(lines) and not lines[cursor].startswith("@@"):
            line = lines[cursor]
            prefix = line[0]

            if prefix not in (" ", "-", "+"):
                raise UnifiedPatchError("patch_invalid_format")

            text = line[1:]
            cursor += 1

            # 标记只作用于紧邻的上一条数据行，不计入任何一侧的行数。
            if (
                cursor < len(lines)
                and lines[cursor] == NO_NEWLINE_MARKER
            ):
                # 空字符串不是一条“末尾无换行”的文件行。
                if text == "\n":
                    raise UnifiedPatchError("patch_invalid_format")

                text = text[:-1]
                cursor += 1

            if prefix in (" ", "-"):
                before.append(text)

            if prefix in (" ", "+"):
                after.append(text)

            if len(before) > old_count or len(after) > new_count:
                raise UnifiedPatchError("patch_hunk_count_mismatch")

        if len(before) != old_count or len(after) != new_count:
            raise UnifiedPatchError("patch_hunk_count_mismatch")

        # 一侧出现无换行行后，该侧不能再有后续行。
        for side in (before, after):
            if any(not line.endswith("\n") for line in side[:-1]):
                raise UnifiedPatchError("patch_invalid_format")

        hunks.append(
            PatchHunk(
                old_index=old_index,
                new_index=new_index,
                before_lines=tuple(before),
                after_lines=tuple(after),
            )
        )

    return tuple(hunks)


def preview_unified_patch(
    *,
    content: str,
    relative_path: str,
    patch: str,
) -> TextPatchCandidate:
    """全部解析和匹配通过后才返回候选；本函数没有外部事务或副作用。"""

    before_byte_count = _validate_text(
        content,
        max_bytes=MAX_EDIT_TEXT_BYTES,
    )
    _validate_relative_path(relative_path)

    original = _split_lf_lines(content)

    if len(original) > MAX_PREVIEW_LINES:
        raise UnifiedPatchError("patch_limit_exceeded")

    hunks = _parse_hunks(
        patch=patch,
        relative_path=relative_path,
    )

    output: list[str] = []
    old_cursor = 0
    previous_start = -1

    for hunk in hunks:
        old_end = hunk.old_index + len(hunk.before_lines)

        # 同一位置的多次插入也拒绝，要求调用方合并成一个变更块。
        if (
            hunk.old_index < old_cursor
            or hunk.old_index <= previous_start
        ):
            raise UnifiedPatchError("patch_hunks_overlap")

        if hunk.old_index > len(original) or old_end > len(original):
            raise UnifiedPatchError("patch_range_out_of_bounds")

        # 未涉及区域原样保留；不重新编码行结束，也不重新搜索位置。
        output.extend(original[old_cursor:hunk.old_index])

        # 前面块的增删会影响新文件位置，不能只验证旧文件行号。
        if hunk.new_index != len(output):
            raise UnifiedPatchError("patch_new_position_mismatch")

        actual = tuple(original[hunk.old_index:old_end])

        if actual != hunk.before_lines:
            raise UnifiedPatchError("patch_context_mismatch")

        output.extend(hunk.after_lines)

        if len(output) > MAX_PREVIEW_LINES:
            raise UnifiedPatchError("patch_limit_exceeded")

        previous_start = hunk.old_index
        old_cursor = old_end

    output.extend(original[old_cursor:])

    if len(output) > MAX_PREVIEW_LINES:
        raise UnifiedPatchError("patch_limit_exceeded")

    # 防止无换行的新末行与后续未修改内容被拼成一行。
    if any(not line.endswith("\n") for line in output[:-1]):
        raise UnifiedPatchError("patch_invalid_format")

    updated_content = "".join(output)
    after_byte_count = _validate_text(
        updated_content,
        max_bytes=MAX_EDIT_TEXT_BYTES,
    )

    if updated_content == content:
        raise UnifiedPatchError("patch_no_change")

    return TextPatchCandidate(
        relative_path=relative_path,
        updated_content=updated_content,
        before_byte_count=before_byte_count,
        after_byte_count=after_byte_count,
        hunk_count=len(hunks),
    )
