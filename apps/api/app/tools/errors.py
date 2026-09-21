"""允许进入工具 Observation 的固定错误码与安全文案。"""


_SAFE_MESSAGES = {
    "command_request_rejected": "命令不符合当前沙箱参数规则；仅支持容器内绝对程序路径和默认临时目录",
    "command_creation_unconfirmed": "沙箱创建结果未确认，不要自动重试",
    "command_execution_unconfirmed": "命令执行结果未确认，需要检查执行状态，不要自动重试",
    "command_timeout_unconfirmed": "命令等待超时，执行与资源状态需要检查，不要自动重试",
    "command_cleanup_unconfirmed": "命令已有结果，但沙箱清理未确认，不要重新执行命令",
    "command_result_unavailable": "命令结果暂不可用，需要检查执行状态，不要自动重试",
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
    "invalid_find_query": (
        "文件名查询须为1～128个字符，"
        "不能包含路径分隔符、换行或NUL"
    ),
    "file_find_unsupported": "当前平台尚不支持受限文件查找",
    "file_find_changed": "目录在查找期间发生变化，请重新查询",
    "file_find_access_denied": "没有权限查找目标目录",
    "file_find_unavailable": "目标或子目录暂时无法查找",
    "invalid_edit_text": "只支持不含NUL的有效UTF-8文本",
    "edit_text_too_large": "文本超过允许预览的大小",
    "empty_old_text": "待替换文本不能为空",
    "edit_no_change": "新旧文本相同，没有需要预览的修改",
    "edit_target_not_found": "原文中没有找到待替换文本",
    "edit_target_ambiguous": (
        "待替换文本存在多个匹配，请提供更完整的上下文"
    ),
    "edit_preview_too_many_lines": "文本行数超过允许预览的范围",
    "edit_preview_unavailable": "文件修改预览暂时无法生成",
    "proposal_binding_changed": (
        "任务或项目目录绑定已变化，本次未保存提案；"
        "请重新确认当前任务和目录"
    ),
    "proposal_save_unconfirmed": (
        "提案保存结果未确认，可能已经保存；"
        "不要自动重复创建，需先核对保存结果"
    ),
    "command_recovery_unavailable": "当前执行无法接收新的命令恢复记录，命令未启动",
}


class SafeToolExecutionError(Exception):
    """只接受白名单错误码，不接收底层异常正文。"""

    def __init__(self, code: str) -> None:
        if code not in _SAFE_MESSAGES:
            raise ValueError("未知的安全工具错误码")

        self.code = code
        self.message = _SAFE_MESSAGES[code]
        super().__init__(self.message)
