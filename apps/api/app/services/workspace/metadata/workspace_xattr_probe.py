"""在自建临时文件中探测xattr复制表现，不授予真实项目写入能力。"""

import ctypes
import sys
from dataclasses import dataclass
from functools import lru_cache
from tempfile import TemporaryFile
from typing import Literal

from app.services.workspace.metadata.workspace_file_xattrs import (
    FileXattr,
    FileXattrError,
    read_file_xattrs,
)


ReadbackStatus = Literal[
    "matched",
    "different",
    "missing",
    "unavailable",
]


class XattrProbeError(ValueError):
    """探测失败使用固定错误，不暴露临时路径或属性值。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AttributeProbeResult:
    # 仅报告名称和比较结果，不返回属性值或摘要。
    name: str
    matched_before: bool
    set_accepted: bool
    readback: ReadbackStatus


@dataclass(frozen=True)
class XattrProbeResult:
    attributes: tuple[AttributeProbeResult, ...]

    # None表示目标最终无法读取；空元组才表示没有额外可见属性。
    extra_target_names: tuple[str, ...] | None

    # 只说明这次临时样例的最终可见快照是否完全相同。
    # 不是其他文件系统、未来执行或不可见属性的能力保证。
    visible_snapshot_equal: bool


@lru_cache(maxsize=1)
def _native_library() -> ctypes.CDLL:
    """独立声明写接口，不改动现有读取或替换模块的能力边界。"""

    if sys.platform != "darwin":
        raise XattrProbeError("xattr_probe_unsupported")

    try:
        library = ctypes.CDLL(
            "/usr/lib/libSystem.B.dylib",
            use_errno=True,
        )
        library.fsetxattr.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.c_int,
        ]
        library.fsetxattr.restype = ctypes.c_int
    except (OSError, AttributeError) as error:
        raise XattrProbeError("xattr_probe_unsupported") from error

    return library


def _set_attribute(
    library: ctypes.CDLL,
    descriptor: int,
    attribute: FileXattr,
) -> bool:
    """仅对本模块拥有的临时描述符调用，不重试、不绕过权限检查。"""

    # 分配非空缓冲区，但传递真实长度；空属性值仍是合法输入。
    buffer = ctypes.create_string_buffer(
        attribute.value,
        max(len(attribute.value), 1),
    )

    # position=0，options=0：不使用任何绕过安全检查的选项。
    return library.fsetxattr(
        descriptor,
        attribute.name,
        buffer,
        len(attribute.value),
        0,
        0,
    ) == 0


def probe_xattr_copy() -> XattrProbeResult:
    """探测完成并关闭临时文件后才返回结果；不缓存探测结论。"""

    library = _native_library()

    # 固定样例覆盖普通值、空值和二进制值。
    # 系统自动附带的属性通过后面的真实读取发现，不硬编码其值。
    samples = (
        FileXattr(
            name=b"com.example.agent_probe.text",
            value=b"sample",
        ),
        FileXattr(
            name=b"com.example.agent_probe.empty",
            value=b"",
        ),
        FileXattr(
            name=b"com.example.agent_probe.binary",
            value=b"\x00\xff\x01",
        ),
    )

    try:
        # 描述符与临时文件生命周期完全归本函数所有。
        # 任一步异常都退出上下文；不接收或覆盖用户已有文件。
        with (
            TemporaryFile(mode="w+b") as source,
            TemporaryFile(mode="w+b") as target,
        ):
            source_fd = source.fileno()
            target_fd = target.fileno()

            for sample in samples:
                if not _set_attribute(library, source_fd, sample):
                    raise XattrProbeError(
                        "xattr_probe_sample_unavailable",
                    )

            source_snapshot = read_file_xattrs(source_fd)
            source_values = {
                item.name: item.value
                for item in source_snapshot
            }

            # 设置样例时同样不能只相信返回码。
            for sample in samples:
                if source_values.get(sample.name) != sample.value:
                    raise XattrProbeError(
                        "xattr_probe_sample_unconfirmed",
                    )

            target_before = {
                item.name: item.value
                for item in read_file_xattrs(target_fd)
            }

            accepted = {}
            for attribute in source_snapshot:
                accepted[attribute.name] = _set_attribute(
                    library,
                    target_fd,
                    attribute,
                )

            # 所有设置结束后统一回读，避免遗漏后续设置对先前属性的影响。
            # 不删除目标额外属性，它们也是兼容性探测的重要证据。
            try:
                target_snapshot = read_file_xattrs(target_fd)
            except FileXattrError:
                target_snapshot = None

            # 复制过程只应读取源文件，不能悄悄接受变化后的源快照。
            if read_file_xattrs(source_fd) != source_snapshot:
                raise XattrProbeError("xattr_probe_source_changed")

            target_values = (
                None
                if target_snapshot is None
                else {
                    item.name: item.value
                    for item in target_snapshot
                }
            )

            results = []
            for attribute in source_snapshot:
                if target_values is None:
                    readback: ReadbackStatus = "unavailable"
                elif attribute.name not in target_values:
                    readback = "missing"
                elif target_values[attribute.name] != attribute.value:
                    readback = "different"
                else:
                    readback = "matched"

                results.append(
                    AttributeProbeResult(
                        name=attribute.name.decode("utf-8"),
                        matched_before=(
                            attribute.name in target_before
                            and target_before[attribute.name]
                            == attribute.value
                        ),
                        set_accepted=accepted[attribute.name],
                        readback=readback,
                    )
                )

            extra_names = (
                None
                if target_values is None
                else tuple(
                    name.decode("utf-8")
                    for name in sorted(
                        target_values.keys() - source_values.keys()
                    )
                )
            )

            result = XattrProbeResult(
                attributes=tuple(results),
                extra_target_names=extra_names,
                visible_snapshot_equal=(
                    target_snapshot is not None
                    and target_snapshot == source_snapshot
                ),
            )

        # 文件关闭或清理失败时不会走到这里，不能返回完整成功回执。
        return result
    except OSError as error:
        raise XattrProbeError("xattr_probe_unavailable") from error
