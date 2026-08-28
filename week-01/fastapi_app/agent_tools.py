from datetime import datetime, timezone

from openai.types.chat import ChatCompletionToolParam


def get_current_time() -> str:
    local_time = datetime.now(timezone.utc).astimezone()
    return local_time.strftime("%Y-%m-%d %H:%M:%S")


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


TOOL_FUNCTIONS = {
    "get_current_time": get_current_time,
}
