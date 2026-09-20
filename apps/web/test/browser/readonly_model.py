"""只读工具链的受控模型；工具、授权、文件与持久化均走真实实现。"""

import json

from app.services.runtime.agent.agent_runtime import (
    FinalAnswer,
    ModelUsage,
    ToolAction,
    ToolErrorObservation,
)


def readonly_decision(prompt, observations):
    # 明确提供模拟用量，继续走生产预算检查，不关闭预算保护。
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)

    def action(name, arguments):
        return ToolAction(
            tool_call_id=f"readonly-{len(observations)}",
            tool_name=name,
            arguments=json.dumps(arguments),
            model_usage=usage,
        )

    if observations and isinstance(observations[-1], ToolErrorObservation):
        return FinalAnswer(
            content=f"只读检查失败：{observations[-1].message}",
            model_usage=usage,
        )
    if not observations:
        if "[readonly-escape]" in prompt:
            return action("read_text_file", {"relative_path": "../outside.txt"})
        return action("list_directory", {"relative_path": "."})
    if len(observations) == 1:
        # 文件名来自真实目录结果，不能直接把夹具内容伪装成工具结果。
        entries = json.loads(observations[0].result)["entries"]
        filename = next(entry["name"] for entry in entries if entry["name"].endswith(".txt"))
        return action("search_text_file", {"relative_path": filename, "query": "TARGET"})
    if len(observations) == 2:
        result = json.loads(observations[1].result)
        return action("read_text_file", {"relative_path": result["relative_path"]})
    search = json.loads(observations[1].result)
    file = json.loads(observations[2].result)
    match = search["matches"][0]
    line = file["content"].splitlines()[match["line_number"] - 1]
    return FinalAnswer(
        content=f"定位到 {search['relative_path']} 第 {match['line_number']} 行：{line}",
        model_usage=usage,
    )
