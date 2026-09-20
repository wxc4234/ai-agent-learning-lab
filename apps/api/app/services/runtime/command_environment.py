"""构造 POSIX 命令环境，不读取宿主环境或访问文件系统。"""

from pathlib import PurePosixPath


# 固定搜索目录，不继承宿主 PATH，也不包含当前工作目录。
# 后续需要其他工具链时，应由服务端显式扩展执行策略。
POSIX_COMMAND_PATH = "/usr/bin:/bin"


def _validate_directory_path(value: str) -> str:
    """校验执行器提供的目标环境绝对路径，只检查语法。"""

    if not isinstance(value, str):
        raise TypeError("执行目录必须是字符串")

    if not value or len(value) > 4096:
        raise ValueError("执行目录路径长度不符合要求")

    # 禁止控制字符及反斜杠，明确采用 POSIX 路径协议。
    # 不 strip，避免悄悄改变实际目录名称。
    if "\\" in value or any(
        ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        raise ValueError("执行目录包含不支持的字符")

    path = PurePosixPath(value)

    # 双斜杠开头在部分系统中可能具有特殊含义，明确拒绝。
    # 根目录也不能充当本课要求的隔离 HOME 或临时目录。
    if (
        not path.is_absolute()
        or value.startswith("//")
        or value == "/"
    ):
        raise ValueError("执行目录必须是非根目录的 POSIX 绝对路径")

    # 拒绝上级引用；不通过规范化将它悄悄折叠。
    if ".." in path.parts:
        raise ValueError("执行目录不能包含上级引用")

    # 要求调用方传入明确的规范语法：
    # 不接受重复斜杠、./ 或尾部斜杠。
    # 这里不调用 resolve，也不验证目录是否真实存在。
    if str(path) != value:
        raise ValueError("执行目录必须使用规范的路径写法")

    return value


def build_posix_command_environment(
    *,
    home_directory: str,
    temporary_directory: str,
) -> dict[str, str]:
    """仅根据服务端提供的目录构造新的环境变量映射。"""

    # 路径应属于命令实际运行的目标环境。
    # 在容器中执行时，必须传容器内路径，而不是宿主挂载源路径。
    home = _validate_directory_path(home_directory)
    temporary = _validate_directory_path(temporary_directory)
    home_path = PurePosixPath(home)

    # 从空白名单构造，不复制 os.environ，也不接收任意 env 覆盖。
    # 每次返回独立字典，避免一次执行的修改污染后续执行。
    return {
        "PATH": POSIX_COMMAND_PATH,
        "HOME": home,
        "TMPDIR": temporary,
        "TMP": temporary,
        "TEMP": temporary,
        "LANG": "C",
        "LC_ALL": "C",
        "XDG_CONFIG_HOME": str(home_path / ".config"),
        "XDG_CACHE_HOME": str(home_path / ".cache"),
        "XDG_DATA_HOME": str(home_path / ".local" / "share"),
        "XDG_STATE_HOME": str(home_path / ".local" / "state"),
    }
