from fastapi import APIRouter, HTTPException
from openai.types.chat import ChatCompletionMessageParam

from app.config import settings
from app.schemas import ToolTestRequest
from app.services.model_client import client
from app.tools.registry import TOOL_FUNCTIONS, TOOLS

# 工具调用单独分组，后续可在这里加入审批、超时、重试和执行轨迹。
router = APIRouter(tags=["tools"])


@router.post("/tool-test")
async def tool_test(request: ToolTestRequest):
    messages: list[ChatCompletionMessageParam] = [
        {
            "role": "user",
            "content": request.prompt,
        }
    ]

    # 第一次调用：让模型决定是否使用工具；模型只能请求，不能直接执行 Python。
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
    tool_function = TOOL_FUNCTIONS.get(tool_name)
    if tool_function is None:
        # 白名单阻止模型构造任意函数名，从而避免任意代码执行。
        raise HTTPException(
            status_code=500,
            detail=f"找不到工具：{tool_name}",
        )

    # 真正的副作用只发生在通过白名单校验之后。
    tool_result = tool_function()

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

    # 第二次调用将工具结果交回模型，由模型组织面向用户的自然语言答案。
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


"""最小 Tool Calling 演示接口，展示模型请求与白名单执行闭环。"""
