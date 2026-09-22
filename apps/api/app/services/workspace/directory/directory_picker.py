"""本机系统目录选择器：固定程序、无 shell 插值、等待期间不持有数据库事务。"""

import json
import subprocess
import sys
from threading import Lock

_PICKER_LOCK = Lock()
PICKER_TIMEOUT_SECONDS = 120

_MAC_SCRIPT = '''
try
    set chosenFolder to choose folder with prompt "选择项目目录"
    return "selected:" & POSIX path of chosenFolder
on error number -128
    return "cancelled:"
end try
'''
_WINDOWS_SCRIPT = '''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Select project directory'
$dialog.ShowNewFolderButton = $false
try {
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        @{path=$dialog.SelectedPath} | ConvertTo-Json -Compress
    } else {
        @{path=$null} | ConvertTo-Json -Compress
    }
} finally { $dialog.Dispose() }
'''


class DirectoryPickerError(Exception):
    """只允许固定错误码跨越服务边界。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def select_directory() -> str | None:
    """返回用户选中的目录，取消返回 None；本地单进程同一时刻只打开一个窗口。"""

    if not _PICKER_LOCK.acquire(blocking=False):
        raise DirectoryPickerError("directory_picker_busy")
    try:
        if sys.platform == "darwin":
            command = ["/usr/bin/osascript", "-e", _MAC_SCRIPT]
        elif sys.platform == "win32":
            command = [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-STA",
                "-Command", _WINDOWS_SCRIPT,
            ]
        else:
            raise DirectoryPickerError("directory_picker_unsupported")

        # 不使用 shell，也不把请求参数拼入脚本；超时会终止并回收子进程。
        result = subprocess.run(
            command, capture_output=True, timeout=PICKER_TIMEOUT_SECONDS,
            check=True, encoding="utf-8",
        )
        if sys.platform == "darwin":
            # 只移除 osascript 自己追加的换行，保留目录名里的合法空白。
            output = result.stdout.removesuffix("\n")
            if output == "cancelled:":
                return None
            if not output.startswith("selected:"):
                raise ValueError("Invalid picker result")
            path = output[len("selected:"):]
        else:
            payload = json.loads(result.stdout)
            path = payload["path"]
            if path is None:
                return None
        if not isinstance(path, str) or not path or "\0" in path:
            raise ValueError("Invalid picker path")
        return path
    except subprocess.TimeoutExpired as error:
        raise DirectoryPickerError("directory_picker_timeout") from error
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        raise DirectoryPickerError("directory_picker_unavailable") from error
    finally:
        _PICKER_LOCK.release()
