"""最小 Tool Calling 演示接口，展示参数校验与白名单执行闭环。"""

from fastapi import APIRouter, HTTPException
from openai.types.chat import ChatCompletionMessageParam
from pydantic import ValidationError

from app.config import settings
from app.schemas import ToolTestRequest
from app.services.model_client import client
from app.tools.registry import TOOL_REGISTRY, TOOLS

router = APIRouter(tags=["tools"])


def build_tool_error(
    *,
    tool_call_id: str,
    tool_name: str,
    code: str,
    message: str,
    details: object | None = None,
) -> dict[str, object]:
    """构造可由 tool_call_id 追踪的工具错误。"""
    error: dict[str, object] = {
        "code": code,
        "message": message,
    }

    if details is not None:
        error["details"] = details

    return {
        "type": "tool_error",
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "error": error,
    }


@router.post("/tool-test")
async def tool_test(request: ToolTestRequest):
    messages: list[ChatCompletionMessageParam] = [
        {
            "role": "user",
            "content": request.prompt,
        }
    ]

    response = await client.chat.completions.create(
        model=settings.deepseek_model,
        messages=messages,
        tools=TOOLS,
        stream=False,
        extra_body={"thinking": {"type": "disabled"}},
    )

    message = response.choices[0].message

    if not message.tool_calls:
        return {
            "type": "text",
            "content": message.content,
        }

    tool_call = message.tool_calls[0]

    if tool_call.type != "function":
        raise HTTPException(
            status_code=500,
            detail="暂不支持当前工具类型",
        )

    tool_name = tool_call.function.name
    tool_definition = TOOL_REGISTRY.get(tool_name)

    if tool_definition is None:
        return build_tool_error(
            tool_call_id=tool_call.id,
            tool_name=tool_name,
            code="unknown_tool",
            message=f"工具未注册：{tool_name}",
        )

    try:
        validated_arguments = tool_definition.validate_arguments(
            tool_call.function.arguments
        )
    except ValidationError as error:
        return build_tool_error(
            tool_call_id=tool_call.id,
            tool_name=tool_name,
            code="invalid_tool_arguments",
            message="工具参数未通过校验",
            details=error.errors(include_url=False),
        )

    tool_result = tool_definition.execute(validated_arguments)

    messages.append(
        {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": tool_call.function.arguments,
                    },
                },
            ],
        }
    )

    messages.append(
        {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": tool_result,
        }
    )

    final_response = await client.chat.completions.create(
        model=settings.deepseek_model,
        messages=messages,
        tools=TOOLS,
        stream=False,
        extra_body={"thinking": {"type": "disabled"}},
    )

    final_reply = final_response.choices[0].message.content or ""

    return {
        "type": "final_answer",
        "tool_name": tool_name,
        "tool_result": tool_result,
        "reply": final_reply,
    }
