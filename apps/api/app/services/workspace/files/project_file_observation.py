"""仅为可信宿主观察文件对象；不创建临时文件，不修改来源或元数据。"""

import os
import stat
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.services.workspace.directory.workspace_path import _parse_relative_path
from app.services.workspace.files.workspace_file_replace import (
    FileReplaceRejected, _identity, _require_supported_platform,
    _verify_directory_chain, _verify_original,
)
from app.services.workspace.metadata.workspace_file_metadata import read_file_metadata


@dataclass(frozen=True, slots=True)
class ProjectFileObservation:
    root_identity: tuple[int, int]
    file_identity: tuple[int, int]


@contextmanager
def observe_project_file(*, bound_root: str, relative_path: str, baseline_sha256: str) -> Generator[ProjectFileObservation, None, None]:
    """描述符覆盖两次观察；调用者的数据库查询结束后才做末次文件复核。

    这里不resolve链接。目录项/摘要/元数据变化会拒绝，但不能保证返回后的
    状态，也无法消除检查间隙内不可观察的ABA；排他写入能力仍未建立。
    """
    _require_supported_platform()
    if len(bound_root) > 4096 or len(relative_path) > 4096:
        raise FileReplaceRejected('file_replace_input_invalid')
    root = Path(bound_root)
    relative = _parse_relative_path(relative_path)
    if (not root.is_absolute() or root == Path(root.anchor) or '..' in root.parts
            or str(root) != bound_root or len(bound_root) > 4096
            or relative.as_posix() != relative_path or not relative.parts):
        raise FileReplaceRejected('file_replace_input_invalid')
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        parent = os.open(root.anchor, directory_flags)
        stack.callback(os.close, parent)
        links: list[tuple[int, str, int]] = []
        root_fd = parent
        for index, component in enumerate(root.parts[1:] + relative.parts[:-1], start=1):
            child = os.open(component, directory_flags, dir_fd=parent)
            stack.callback(os.close, child)
            links.append((parent, component, child))
            parent = child
            if index == len(root.parts) - 1:
                root_fd = child
        original = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(original.st_mode) or original.st_nlink != 1
                or original.st_uid != os.geteuid() or not original.st_mode & stat.S_IWUSR
                or original.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)):
            raise FileReplaceRejected('file_replace_target_unsupported')
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        stack.callback(os.close, descriptor)

        def verify() -> None:
            _verify_directory_chain(links)
            _verify_original(parent, relative.name, descriptor, original, baseline_sha256)

        verify()
        metadata = read_file_metadata(descriptor)
        verify()
        observation = ProjectFileObservation(_identity(os.fstat(root_fd)), _identity(original))
        yield observation
        # 文件身份之外核对ACL、mode/owner与属性；返回值不包含这些私有正文。
        if read_file_metadata(descriptor) != metadata:
            raise FileReplaceRejected('file_replace_metadata_changed')
        verify()
