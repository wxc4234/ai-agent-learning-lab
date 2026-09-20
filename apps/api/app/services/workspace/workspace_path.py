"""从可信任务归属解析 Workspace 内的现有路径，不读取文件内容。"""

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from app.database import SessionLocal
from app.services.tasks.task_workspace import owned_task
from app.services.workspace.workspace_directory import (
    validate_workspace_directory,
)


WorkspacePathErrorCode = Literal[
    "workspace_directory_unbound",
    "workspace_directory_changed",
    "invalid_relative_path",
    "absolute_path_not_allowed",
    "parent_path_not_allowed",
    "path_outside_workspace",
    "workspace_path_not_found",
    "workspace_path_access_denied",
    "workspace_path_unavailable",
]


class WorkspacePathError(ValueError):
    """提供稳定错误码，不把底层异常中的本机路径暴露给模型。"""

    def __init__(
        self,
        code: WorkspacePathErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


def _parse_relative_path(raw_path: str) -> PurePosixPath:
    """按统一的相对路径协议解析输入，不访问文件系统。"""

    # 不使用 strip，避免悄悄改变实际文件名。
    if (
        not isinstance(raw_path, str)
        or not raw_path
        or "\x00" in raw_path
    ):
        raise WorkspacePathError(
            "invalid_relative_path",
            "请提供有效的项目内相对路径",
        )

    posix_path = PurePosixPath(raw_path)
    windows_path = PureWindowsPath(raw_path)

    # Windows 的 C:foo 不是绝对路径，但依赖盘符当前目录，也必须拒绝。
    # root 还可识别 \foo；drive 可识别盘符与 UNC 共享。
    if posix_path.is_absolute() or windows_path.drive or windows_path.root:
        raise WorkspacePathError(
            "absolute_path_not_allowed",
            "只能使用项目内相对路径，不能指定盘符或根路径",
        )

    # 统一使用正斜杠，避免反斜杠在 Unix 与 Windows 上含义不同。
    if "\\" in raw_path:
        raise WorkspacePathError(
            "invalid_relative_path",
            "相对路径必须使用正斜杠分隔",
        )

    # 拒绝全部上级引用，包括最终仍可能回到项目内的 a/../b。
    # 不提前折叠 ..，因为它与符号链接组合时会改变实际路径含义。
    if ".." in posix_path.parts:
        raise WorkspacePathError(
            "parent_path_not_allowed",
            "相对路径不能包含上级目录引用",
        )

    # 冒号可能表示 Windows 替代数据流；设备名也不是普通项目文件。
    # 对所有平台采用一致规则，避免输入切换平台后改变含义。
    invalid_characters = '<>:"|?*'
    for part in posix_path.parts:
        if (
            any(
                character in invalid_characters or ord(character) < 32
                for character in part
            )
            or part.endswith((" ", "."))
            or PureWindowsPath(part).is_reserved()
        ):
            raise WorkspacePathError(
                "invalid_relative_path",
                "路径包含不支持的名称或字符",
            )

    return posix_path


def _resolve_bound_path(
    bound_root: str,
    relative_path: PurePosixPath,
) -> Path:
    """检查根目录和目标的当前状态，返回规范路径快照。"""

    # 绑定时检查通过，不代表当前目录仍然存在或仍指向同一位置。
    # 目录校验失败沿用 WorkspaceDirectoryError 的安全错误分类。
    root = validate_workspace_directory(bound_root)

    # 数据库保存的是绑定时的规范路径。
    # 若该路径后来被改成指向其他位置的链接，不能默默扩大授权范围。
    if root != Path(bound_root):
        raise WorkspacePathError(
            "workspace_directory_changed",
            "绑定目录的实际位置已变化，暂时不能访问",
        )

    try:
        # 按路径段拼接，避免把模型输入当成本机绝对路径。
        # strict=True 要求目标存在，并解析中间目录和末端符号链接。
        target = root.joinpath(*relative_path.parts).resolve(strict=True)

    except (FileNotFoundError, NotADirectoryError):
        raise WorkspacePathError(
            "workspace_path_not_found",
            "目标不存在，或路径中包含非目录项",
        ) from None

    except PermissionError:
        raise WorkspacePathError(
            "workspace_path_access_denied",
            "没有权限访问目标路径",
        ) from None

    except (OSError, RuntimeError, ValueError):
        # 包含循环符号链接、非法文件系统路径及其他解析失败。
        # 不返回原始异常文本，防止泄露 Workspace 外的路径。
        raise WorkspacePathError(
            "workspace_path_unavailable",
            "目标路径暂时无法解析",
        ) from None

    # 比较的是解析后的路径层级，而不是字符串前缀。
    # 项目内链接允许，解析后落到项目外的链接拒绝。
    if not target.is_relative_to(root):
        raise WorkspacePathError(
            "path_outside_workspace",
            "目标路径超出了当前项目目录",
        )

    return target


def resolve_task_workspace_path(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    relative_path: str,
) -> Path:
    """先授权任务，再解析该任务绑定目录内的现有路径。"""

    # 身份来自服务端认证上下文；任务定位来自当前执行上下文。
    # 这些字段不应进入允许模型自由填写的文件工具参数。
    with SessionLocal() as session:
        task, _ = owned_task(
            session,
            user_id,
            workspace_id,
            task_id,
        )

        # 在 Session 关闭前复制普通字段，不向外返回 ORM 对象。
        bound_root = task.workspace.root_path

    # 以上只有查询，不需要 commit。
    # 先结束数据库事务，再访问文件系统，避免文件系统等待占用事务。
    if bound_root is None:
        raise WorkspacePathError(
            "workspace_directory_unbound",
            "当前任务所属项目尚未绑定本地目录",
        )

    parsed_path = _parse_relative_path(relative_path)

    return _resolve_bound_path(
        bound_root=bound_root,
        relative_path=parsed_path,
    )
