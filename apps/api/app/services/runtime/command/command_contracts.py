"""受限命令的数据契约；本模块不访问文件系统或启动进程。"""

from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# 服务端固定策略，不放入模型可提交的请求字段。
COMMAND_TIMEOUT_SECONDS = 30
MAX_COMMAND_ARGUMENTS = 64
MAX_ARGUMENT_CHARACTERS = 4096
MAX_TOTAL_ARGUMENT_CHARACTERS = 16_384

# 原始输出按字节限制捕获，展示文本另设字符上限。
# 后续执行器必须分别实施这些限制，声明常量本身不会限制资源。
MAX_CAPTURE_BYTES_PER_STREAM = 65_536
MAX_OUTPUT_CHARACTERS_PER_STREAM = 65_536


# 参数允许空字符串：例如程序可能需要一个明确的空参数。
# argv[0] 是程序名，下面会单独要求它非空。
CommandArgument = Annotated[
    str,
    Field(max_length=MAX_ARGUMENT_CHARACTERS),
]


class CommandRequest(BaseModel):
    """模型只描述程序参数与项目内工作目录。"""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    argv: list[CommandArgument] = Field(
        min_length=1,
        max_length=MAX_COMMAND_ARGUMENTS,
        description=(
            "程序与参数组成的数组；第一项是程序，"
            "后续各项分别是一个参数，不自动进行 Shell 展开"
        ),
    )
    working_directory: str = Field(
        default=".",
        min_length=1,
        max_length=4096,
        description="相对于当前项目根目录的工作目录，默认使用项目根目录",
    )

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, value: list[str]) -> list[str]:
        # 不 strip 或拆分参数，避免改变调用方表达的参数边界。
        if not value[0] or value[0].isspace():
            raise ValueError("程序名称不能为空")

        # 操作系统的进程参数不能包含 NUL。
        if any("\x00" in argument for argument in value):
            raise ValueError("命令参数不能包含 NUL 字符")

        # 单项和数量限制之外，再限制整个请求的参数字符总量。
        if sum(len(argument) for argument in value) > (
            MAX_TOTAL_ARGUMENT_CHARACTERS
        ):
            raise ValueError("命令参数总长度超过限制")

        return value

    @field_validator("working_directory")
    @classmethod
    def validate_working_directory(cls, value: str) -> str:
        # 这里只做路径语法校验，不确认目录存在、归属或符号链接目标。
        posix_path = PurePosixPath(value)
        windows_path = PureWindowsPath(value)

        if (
            posix_path.is_absolute()
            or windows_path.drive
            or windows_path.root
        ):
            raise ValueError("工作目录必须是项目内相对路径")

        if "\\" in value or "\x00" in value:
            raise ValueError("工作目录必须使用正斜杠且不能包含 NUL")

        # 不折叠上级引用，即使 a/../b 看起来最终仍在项目内也拒绝。
        if ".." in posix_path.parts:
            raise ValueError("工作目录不能包含上级引用")

        # 与现有项目路径协议保持一致，避免跨平台名称含义不同。
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
                raise ValueError("工作目录包含不支持的名称或字符")

        return value


CommandStatus = Literal[
    "exited",
    "timed_out",
    "cancelled",
    "start_failed",
]

CommandStartErrorCode = Literal[
    "working_directory_unavailable",
    "executable_unavailable",
    "permission_denied",
    "sandbox_unavailable",
    "process_start_failed",
]


class CommandResult(BaseModel):
    """执行器产生的结果；模型不能通过请求指定这些字段。"""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
    )

    status: CommandStatus

    # exited 必须有退出码；超时或取消后也可能取得实际退出码。
    # 不限定为非负数，为 POSIX 信号导致的负返回码保留表达空间。
    exit_code: int | None = None

    stdout: str = Field(
        default="",
        max_length=MAX_OUTPUT_CHARACTERS_PER_STREAM,
    )
    stderr: str = Field(
        default="",
        max_length=MAX_OUTPUT_CHARACTERS_PER_STREAM,
    )

    # 两路输出分别记录是否丢弃内容，不能用一个标记代替。
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    duration_ms: int = Field(ge=0)

    # 只记录固定启动失败分类，不把底层异常正文塞进输出。
    start_error_code: CommandStartErrorCode | None = None

    @model_validator(mode="after")
    def validate_result_consistency(self) -> Self:
        if self.status == "start_failed":
            if self.start_error_code is None:
                raise ValueError("启动失败必须提供固定错误分类")

            if self.exit_code is not None:
                raise ValueError("未启动的进程不能具有退出码")

            if (
                self.stdout
                or self.stderr
                or self.stdout_truncated
                or self.stderr_truncated
            ):
                raise ValueError("启动失败不能包含进程输出或截断标记")

            return self

        if self.start_error_code is not None:
            raise ValueError("已启动的命令不能包含启动失败分类")

        if self.status == "exited" and self.exit_code is None:
            raise ValueError("已退出的命令必须提供退出码")

        return self
