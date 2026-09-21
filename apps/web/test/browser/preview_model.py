"""预览浏览器受控模型；读取和预览执行均为真实服务。"""

import json

from app.services.runtime.agent.agent_runtime import FinalAnswer, ModelUsage, ToolAction, ToolErrorObservation


def preview_decision(prompt, observations):
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    if not observations:
        marker = next(name for name in ("success", "delete", "ambiguous", "truncated") if f"[preview-{name}]" in prompt)
        return ToolAction(
            tool_call_id="preview-browser",
            tool_name="preview_file_edit",
            arguments=json.dumps({
                "relative_path": f"{marker}.txt",
                "old_text": "old",
                "new_text": "" if marker == "delete" else "new",
            }),
            model_usage=usage,
        )
    observation = observations[-1]
    if isinstance(observation, ToolErrorObservation):
        return FinalAnswer(content=f"预览失败：{observation.message}", model_usage=usage)
    result = json.loads(observation.result)
    assert result["status"] == "preview_only"
    suffix = "，Diff不完整" if result["diff_truncated"] else ""
    return FinalAnswer(content=f"仅生成预览，尚未写入{suffix}", model_usage=usage)
