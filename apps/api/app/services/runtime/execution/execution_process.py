"""本机进程存活证据：无法确认时拒绝恢复，不按占用年龄推断。"""

import hashlib
import os
from pathlib import Path
import platform
import re
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def host_identity() -> str | None:
    """使用系统机器标识；Linux 额外区分 PID namespace，避免容器 PID 混淆。"""
    try:
        system = platform.system()
        if system == 'Darwin':
            output = subprocess.check_output(
                ['/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'],
                text=True, timeout=5,
            )
            match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', output)
            if not match:
                return None
            identity = match.group(1)
        elif system == 'Linux':
            identity = Path('/etc/machine-id').read_text().strip()
            identity += ':' + os.readlink('/proc/self/ns/pid')
        elif system == 'Windows':
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography') as key:
                identity = winreg.QueryValueEx(key, 'MachineGuid')[0]
        else:
            return None
        if not identity:
            return None
        return hashlib.sha256(f'{system}:{identity}'.encode()).hexdigest()
    except (OSError, subprocess.SubprocessError):
        return None


def process_is_dead(host: str | None, pid: int | None) -> bool:
    """只接受本机 PID 已不存在的证据；PID 复用、权限拒绝均保守拒绝。"""
    if not host or host != host_identity() or pid is None or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 87
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value != 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False
