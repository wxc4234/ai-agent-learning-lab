"""文件查找浏览器夹具：仅控制模型决策，消费真实工具结果。"""

import json

from app.services.runtime.agent.agent_runtime import (
    FinalAnswer,
    ModelUsage,
    ToolAction,
    ToolErrorObservation,
)


def find_decision(prompt, observations):
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)

    def action(name, arguments):
        return ToolAction(
            tool_call_id=f"find-{len(observations)}",
            tool_name=name,
            arguments=json.dumps(arguments),
            model_usage=usage,
        )

    if observations and isinstance(observations[-1], ToolErrorObservation):
        return FinalAnswer(
            content=f"查找失败：{observations[-1].message}", model_usage=usage,
        )
    if not observations:
        if "[find-escape]" in prompt:
            return action("find_files", {"query": "outside", "relative_path": "../"})
        if "[find-empty]" in prompt:
            return action("find_files", {"query": "absent-file"})
        if "[find-truncated]" in prompt:
            return action("find_files", {"query": "bulk", "relative_path": "many"})
        return action("find_files", {"query": "定位"})
    result = json.loads(observations[0].result)
    if result["truncated"]:
        return FinalAnswer(content=f"查找未完整覆盖，已返回{len(result['paths'])}个文件", model_usage=usage)
    if not result["paths"]:
        return FinalAnswer(content="当前查找范围内没有匹配文件", model_usage=usage)
    if len(observations) == 1:
        return action("read_text_file", {"relative_path": result["paths"][0]})
    file = json.loads(observations[-1].result)
    return FinalAnswer(
        content=f"读取 {file['relative_path']}：{file['content'].strip()}", model_usage=usage,
    )
