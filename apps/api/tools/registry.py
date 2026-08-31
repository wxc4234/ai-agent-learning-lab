from datetime import datetime, timezone

from openai.types.chat import ChatCompletionToolParam


def get_current_time() -> str:
    """返回服务器所在时区的当前时间，供最小 Tool Calling 流程验证。"""
    local_time = datetime.now(timezone.utc).astimezone()
    return local_time.strftime("%Y-%m-%d %H:%M:%S")


# JSON Schema 告诉模型“可用工具及参数”，但不赋予实际执行权限。
TOOLS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取服务器当前日期和时间",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    }
]


# 名称到函数的白名单是执行边界：未知名称绝不能被动态导入或 eval 执行。
TOOL_FUNCTIONS = {
    "get_current_time": get_current_time,
}
"""工具描述与 Python 执行白名单。模型只能调用这里显式注册的工具。"""
