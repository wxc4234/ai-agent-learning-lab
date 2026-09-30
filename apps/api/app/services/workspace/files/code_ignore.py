"""有明确支持边界的 .gitignore 规则；不执行 Git 或读取全局配置。"""

from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import cache


MAX_IGNORE_BYTES = 16 * 1024
MAX_IGNORE_LINES = 200
MAX_PATTERN_BYTES = 1024
MAX_PATTERN_PARTS = 16


class CodeIgnoreError(ValueError):
    """未知语法不能静默当作不匹配，否则可能纳入应忽略的文件。"""

    code = "unsupported_gitignore"

    def __init__(self) -> None:
        super().__init__("忽略规则超出当前支持范围，未返回源码清单")


@dataclass(frozen=True)
class IgnoreRule:
    # base 相对于绑定根；嵌套规则仅作用于所在目录及其后代。
    base: tuple[str, ...]
    parts: tuple[bytes, ...]
    anchored: bool
    directories_only: bool
    negated: bool

    def matches(self, path: tuple[str, ...], is_directory: bool) -> bool:
        if path[: len(self.base)] != self.base or len(path) <= len(self.base):
            return False
        if self.directories_only and not is_directory:
            return False
        relative = tuple(part.encode("utf-8") for part in path[len(self.base) :])
        if not self.anchored:
            return fnmatchcase(relative[-1], self.parts[0])
        return _match_path(self.parts, relative)


def _match_path(pattern: tuple[bytes, ...], path: tuple[bytes, ...]) -> bool:
    # 按路径段匹配，普通 * 和 ? 不跨 /；缓存避免多个 ** 产生指数回溯。
    @cache
    def match(i: int, j: int) -> bool:
        if i == len(pattern):
            return j == len(path)
        if pattern[i] == b"**":
            if i == len(pattern) - 1:
                # a/** 匹配 a 的内容，不匹配 a 目录自身，避免错误剪枝。
                return j < len(path)
            return match(i + 1, j) or (j < len(path) and match(i, j + 1))
        return (
            j < len(path) and fnmatchcase(path[j], pattern[i]) and match(i + 1, j + 1)
        )

    return match(0, 0)


def _validate_component(component: str) -> None:
    if (
        not component
        or component in {".", ".."}
        or ("**" in component and component != "**")
    ):
        raise CodeIgnoreError()
    index = 0
    while index < len(component):
        character = component[index]
        if character == "[":
            end = component.find("]", index + 1)
            if end < 0:
                raise CodeIgnoreError()
            body = component[index + 1 : end]
            body = body.removeprefix("!")
            # 仅支持普通 ASCII 字符集/升序范围，不接受 POSIX 类或复杂转义。
            if not body or any(ord(item) > 127 or item in "[^" for item in body):
                raise CodeIgnoreError()
            for offset, item in enumerate(body):
                if (
                    item == "-"
                    and 0 < offset < len(body) - 1
                    and (
                        body[offset - 1] >= body[offset + 1]
                        or body[offset - 1] == "-"
                        or body[offset + 1] == "-"
                    )
                ):
                    raise CodeIgnoreError()
            index = end + 1
            continue
        if character == "]":
            raise CodeIgnoreError()
        index += 1


def parse_code_ignore(
    content: str, *, base: tuple[str, ...] = ()
) -> tuple[IgnoreRule, ...]:
    """支持常见通配、目录/根锚定、否定与嵌套规则；其他形式明确拒绝。"""

    if len(content.encode("utf-8")) > MAX_IGNORE_BYTES:
        raise CodeIgnoreError()
    lines = content.split("\n")
    if lines[-1] == "":
        lines.pop()
    if len(lines) > MAX_IGNORE_LINES:
        raise CodeIgnoreError()
    rules = []
    for raw in lines:
        raw = raw.removesuffix("\r")
        # 不 strip 前导空格；仅忽略 Git 定义的未转义尾部空格。
        pattern = raw.rstrip(" ")
        if not pattern or pattern.startswith("#"):
            continue
        if len(pattern.encode("utf-8")) > MAX_PATTERN_BYTES or any(
            ord(char) < 32 or char == "\ufeff" for char in pattern
        ):
            raise CodeIgnoreError()
        escaped_prefix = pattern.startswith(("\\#", "\\!"))
        if escaped_prefix:
            pattern = pattern[1:]
        if "\\" in pattern:
            raise CodeIgnoreError()
        negated = not escaped_prefix and pattern.startswith("!")
        if negated:
            pattern = pattern[1:]
        anchored = pattern.startswith("/")
        directories_only = pattern.endswith("/")
        if anchored:
            pattern = pattern[1:]
        if directories_only:
            pattern = pattern[:-1]
        parts = pattern.split("/")
        if len(parts) > MAX_PATTERN_PARTS:
            raise CodeIgnoreError()
        for component in parts:
            _validate_component(component)
        rules.append(
            IgnoreRule(
                base=base,
                parts=tuple(part.encode("utf-8") for part in parts),
                anchored=anchored or len(parts) > 1,
                directories_only=directories_only,
                negated=negated,
            )
        )
    return tuple(rules)


def ignored_by_rules(
    path: tuple[str, ...], *, is_directory: bool, rules: tuple[IgnoreRule, ...]
) -> bool:
    ignored = False
    # 父级在前、子级在后，同一级最后命中规则生效。
    # 调用方剪枝被忽略目录，因此子级 ! 不能恢复被排除父目录中的文件。
    for rule in rules:
        if rule.matches(path, is_directory):
            ignored = not rule.negated
    return ignored
