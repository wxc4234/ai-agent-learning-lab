"""允许进入工具 Observation 的固定错误码与安全文案。"""


_SAFE_MESSAGES = {
    "workspace_not_accessible": "当前任务或项目不可访问",
    "workspace_directory_unavailable": "项目目录未就绪或暂时不可访问",
    "workspace_path_rejected": "路径不存在、不可访问或不符合项目路径规则",
    "workspace_directory_unbound": "当前项目尚未绑定本地目录",
    "file_read_unsupported": "当前平台尚不支持受限文件读取",
    "file_not_regular": "只能读取普通文本文件",
    "file_too_large": "文件超过允许读取的大小",
    "file_not_utf8_text": "只支持不含 NUL 字节的 UTF-8 文本",
    "file_changed": "文件在读取期间发生变化，请重新读取",
    "file_unavailable": "目标文件暂时无法读取",
    "directory_listing_unsupported": "当前平台尚不支持受限目录枚举",
    "directory_listing_not_found": "目标目录已不存在",
    "directory_listing_not_directory": "目标或路径中的某一项不是可打开的目录",
    "directory_listing_access_denied": "没有权限枚举目标目录",
    "directory_listing_changed": "目录在枚举期间发生变化，请重新查询",
    "directory_listing_unavailable": "目标目录暂时无法枚举",
    "invalid_search_query": "查询须为 1～128 个字符，且不能包含换行或 NUL 字符",
}


class SafeToolExecutionError(Exception):
    """只接受白名单错误码，不接收底层异常正文。"""

    def __init__(self, code: str) -> None:
        if code not in _SAFE_MESSAGES:
            raise ValueError("未知的安全工具错误码")

        self.code = code
        self.message = _SAFE_MESSAGES[code]
        super().__init__(self.message)
