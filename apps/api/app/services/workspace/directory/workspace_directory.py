"""校验本地项目目录，返回规范路径，不修改文件系统或数据库。"""

import stat
from pathlib import Path
from typing import Literal

DirectoryErrorCode = Literal[
    "invalid_directory_path",
    "directory_not_found",
    "directory_access_denied",
    "directory_unavailable",
    "not_a_directory",
    "root_directory_not_allowed",
]


class WorkspaceDirectoryError(ValueError):
    """携带稳定错误码，方便后续 HTTP 层转换为安全响应。"""

    def __init__(self, code: DirectoryErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_workspace_directory(raw_path: str) -> Path:
    """检查当前目录状态，返回解析符号链接后的绝对目录路径。"""

    # 不自动 strip：目录名称可以包含空格，不能悄悄改变用户选择。
    # 空字符串与 NUL 字符则不属于有效的目录输入。
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        raise WorkspaceDirectoryError(
            "invalid_directory_path",
            "请输入有效的项目目录路径",
        )

    candidate = Path(raw_path)

    # 相对路径会依赖后端启动目录，导致相同输入指向不同位置。
    # 本课也不展开 ~ 或环境变量，由调用方提供明确的绝对路径。
    if not candidate.is_absolute():
        raise WorkspaceDirectoryError(
            "invalid_directory_path",
            "项目目录必须使用绝对路径",
        )

    try:
        # 严格解析要求路径存在，并将符号链接解析到真实目标。
        # 后续绑定保存这个结果，不保存仍可能改变指向的链接入口。
        resolved = candidate.resolve(strict=True)

        # 使用 stat 获取目录类型，同时保留权限错误等异常的分类。
        # 这里只读取元信息，不尝试创建文件来证明目录可写。
        directory_stat = resolved.stat()

    except (FileNotFoundError, NotADirectoryError):
        raise WorkspaceDirectoryError(
            "directory_not_found",
            "项目目录不存在，或路径中包含非目录项",
        ) from None

    except PermissionError:
        raise WorkspaceDirectoryError(
            "directory_access_denied",
            "没有权限访问项目目录",
        ) from None

    except RuntimeError:
        # Python 3.12 在解析循环符号链接时可能抛出 RuntimeError。
        raise WorkspaceDirectoryError(
            "invalid_directory_path",
            "项目目录路径无法解析，请检查符号链接",
        ) from None

    except OSError:
        # 其他文件系统异常不直接暴露底层错误文本。
        raise WorkspaceDirectoryError(
            "directory_unavailable",
            "暂时无法访问项目目录",
        ) from None

    if not stat.S_ISDIR(directory_stat.st_mode):
        raise WorkspaceDirectoryError(
            "not_a_directory",
            "请选择目录，而不是文件",
        )

    # 拒绝 Unix 根目录或 Windows 盘符根目录等过大的项目范围。
    if resolved == Path(resolved.anchor):
        raise WorkspaceDirectoryError(
            "root_directory_not_allowed",
            "不能将文件系统根目录作为项目目录",
        )

    # 这是检查时刻的路径结果，不是持续有效的文件访问授权。
    # 真正执行文件操作时仍需检查范围与文件系统变化。
    return resolved
