"""生成单次精确文本替换预览，不访问文件系统或执行写入。"""

import json
import re
from dataclasses import dataclass
from difflib import unified_diff
from typing import Literal


# 原文、替换片段和修改后的内容均限制为256 KiB UTF-8。
MAX_EDIT_TEXT_BYTES = 256 * 1024

# Diff算法也需要输入规模边界，不能只限制最终展示长度。
MAX_PREVIEW_LINES = 4_000
MAX_DIFF_CHARACTERS = 16_384
DIFF_CONTEXT_LINES = 3

EditPreviewErrorCode = Literal[
    "invalid_edit_text",
    "edit_text_too_large",
    "empty_old_text",
    "edit_no_change",
    "edit_target_not_found",
    "edit_target_ambiguous",
    "edit_preview_too_many_lines",
]


class EditPreviewError(ValueError):
    """只提供固定分类，不在错误正文中回显文件内容。"""

    def __init__(
        self,
        code: EditPreviewErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TextEditPreview:
    """完整修改结果与有限展示数据；不表示修改已经写入。"""

    updated_content: str
    before_byte_count: int
    after_byte_count: int

    # 这是供审阅的Diff，不是可直接交给git apply的补丁。
    diff: str
    diff_truncated: bool


def _validate_text(value: str) -> int:
    """验证文本契约，返回严格UTF-8编码后的字节数。"""

    if not isinstance(value, str) or "\x00" in value:
        raise EditPreviewError(
            "invalid_edit_text",
            "只支持不含NUL的有效UTF-8文本",
        )

    # UTF-8字节数不会少于字符数，先挡住明显超限的输入，
    # 避免对任意大的字符串再分配编码结果。
    if len(value) > MAX_EDIT_TEXT_BYTES:
        raise EditPreviewError(
            "edit_text_too_large",
            "文本超过允许预览的大小",
        )

    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        # Python字符串可能包含孤立代理项，不能写成有效UTF-8。
        raise EditPreviewError(
            "invalid_edit_text",
            "只支持不含NUL的有效UTF-8文本",
        ) from None

    if len(encoded) > MAX_EDIT_TEXT_BYTES:
        raise EditPreviewError(
            "edit_text_too_large",
            "文本超过允许预览的大小",
        )

    return len(encoded)


def _review_lines(content: str) -> list[str]:
    """把原始行变成可审阅表示，明确展示换行和控制字符。"""

    # 只把CRLF、CR和LF视为行结束，不把其他Unicode字符拆成新行。
    # 捕获组保留分隔符，后面重新拼回每行，避免丢失换行事实。
    parts = re.split(r"(\r\n|\r|\n)", content)
    line_count = len(parts) // 2 + bool(parts[-1])

    if line_count > MAX_PREVIEW_LINES:
        raise EditPreviewError(
            "edit_preview_too_many_lines",
            "文本行数超过允许预览的范围",
        )

    lines: list[str] = []

    for index in range(0, len(parts) - 1, 2):
        original_line = parts[index] + parts[index + 1]

        # JSON字符串表示让CRLF、LF、Tab和末尾无换行的差别可见。
        # 最后的真实LF只服务于Diff排版，不修改文件内容。
        lines.append(
            json.dumps(original_line, ensure_ascii=False) + "\n"
        )

    if parts[-1]:
        lines.append(
            json.dumps(parts[-1], ensure_ascii=False) + "\n"
        )

    return lines


def _build_review_diff(
    before: str,
    after: str,
) -> tuple[str, bool]:
    """生成有界审阅Diff，只截断展示，不截断修改后的内容。"""

    before_lines = _review_lines(before)
    after_lines = _review_lines(after)

    pieces: list[str] = []
    total = 0

    # 使用固定标签，不接受可能包含换行或宿主路径的文件名。
    generated = unified_diff(
        before_lines,
        after_lines,
        fromfile="before",
        tofile="after",
        n=DIFF_CONTEXT_LINES,
        lineterm="\n",
    )

    for piece in generated:
        remaining = MAX_DIFF_CHARACTERS - total

        if len(piece) > remaining:
            if remaining:
                pieces.append(piece[:remaining])

            # 只有实际发现未展示的字符，才标记截断。
            return "".join(pieces), True

        pieces.append(piece)
        total += len(piece)

    return "".join(pieces), False


def preview_text_replacement(
    *,
    content: str,
    old_text: str,
    new_text: str,
) -> TextEditPreview:
    """替换唯一匹配并返回预览；不读取或修改任何外部资源。"""

    before_byte_count = _validate_text(content)
    _validate_text(old_text)
    _validate_text(new_text)

    if not old_text:
        raise EditPreviewError(
            "empty_old_text",
            "待替换文本不能为空",
        )

    if old_text == new_text:
        raise EditPreviewError(
            "edit_no_change",
            "新旧文本相同，没有需要预览的修改",
        )

    position = content.find(old_text)

    if position < 0:
        raise EditPreviewError(
            "edit_target_not_found",
            "原文中没有找到待替换文本",
        )

    # 从第一个匹配的下一字符继续查找，包含重叠匹配。
    # 例如aaa中的aa有两个候选位置，不能擅自选择第一个。
    if content.find(old_text, position + 1) >= 0:
        raise EditPreviewError(
            "edit_target_ambiguous",
            "待替换文本存在多个匹配，请提供更完整的上下文",
        )

    # 按唯一定位切片，不做strip、换行归一化或模糊匹配。
    updated_content = (
        content[:position]
        + new_text
        + content[position + len(old_text):]
    )

    # 新文本本身未超限，也可能使修改后的整体文件超限。
    after_byte_count = _validate_text(updated_content)

    diff, diff_truncated = _build_review_diff(
        before=content,
        after=updated_content,
    )

    return TextEditPreview(
        updated_content=updated_content,
        before_byte_count=before_byte_count,
        after_byte_count=after_byte_count,
        diff=diff,
        diff_truncated=diff_truncated,
    )
