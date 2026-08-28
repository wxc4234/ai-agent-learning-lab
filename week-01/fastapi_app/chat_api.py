import os
from pathlib import Path

from agent_tools import TOOL_FUNCTIONS, TOOLS
from database import (
    init_db,
    load_conversation,
    save_conversation_turn,
)
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from openai import AsyncOpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[1]

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(APP_DIR / ".env")

app = FastAPI()

init_db()

client = AsyncOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

conversations: dict[str, list[ChatCompletionMessageParam]] = {}

MAX_ROUNDS = 5


class ChatRequest(BaseModel):
    session_id: str
    prompt: str


class ChatResponse(BaseModel):
    reply: str


class ToolTestRequest(BaseModel):
    prompt: str


@app.get("/")
def home():
    return {
        "status": "ok",
        "message": "AI 对话服务已启动",
        "docs": "/docs",
    }


@app.get("/chat")
def chat_help():
    return {
        "message": "聊天接口需要使用 POST 请求",
        "docs": "/docs",
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    if request.session_id not in conversations:
        conversations[request.session_id] = [
            {
                "role": "system",
                "content": "你是一个贴心并且简洁的 AI 助手。",
            }
        ]

        saved_history = load_conversation(session_id=request.session_id)

        for message in saved_history:
            if message["role"] == "user":
                conversations[request.session_id].append(
                    {
                        "role": "user",
                        "content": message["content"],
                    }
                )
            elif message["role"] == "assistant":
                conversations[request.session_id].append(
                    {
                        "role": "assistant",
                        "content": message["content"],
                    }
                )

    history = conversations[request.session_id]
    history.append(
        {
            "role": "user",
            "content": request.prompt,
        }
    )

    recent_history = history[1:-1][-(MAX_ROUNDS * 2) :]
    messages_to_send: list[ChatCompletionMessageParam] = [
        history[0],
        *recent_history,
        history[-1],
    ]

    try:
        response = await client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=messages_to_send,
            stream=False,
        )
        reply = response.choices[0].message.content or ""

        save_conversation_turn(
            session_id=request.session_id,
            user_content=request.prompt,
            assistant_content=reply,
        )

        history.append(
            {
                "role": "assistant",
                "content": reply,
            }
        )
        max_saved_messages = MAX_ROUNDS * 2
        if len(history) > max_saved_messages + 1:
            del history[1:-max_saved_messages]

        return {
            "reply": reply,
        }
    except OpenAIError:
        history.pop()
        raise HTTPException(
            status_code=502,
            detail="模型服务暂时不可用",
        )


@app.get("/sessions/{session_id}/messages")
def get_session_message(session_id: str):
    messages = load_conversation(session_id)

    if not messages:
        raise HTTPException(
            status_code=404,
            detail="该会话不存在",
        )

    return {
        "session_id": session_id,
        "total": len(messages),
        "messages": messages,
    }


@app.post("/tool-test")
async def tool_test(request: ToolTestRequest):
    messages: list[ChatCompletionMessageParam] = [
        {
            "role": "user",
            "content": request.prompt,
        }
    ]

    # 第一次调用：让模型决定是否使用工具
    response = await client.chat.completions.create(
        model="deepseek-v4-flash",
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
        raise HTTPException(
            status_code=500,
            detail=f"找不到工具：{tool_name}",
        )

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

    final_response = await client.chat.completions.create(
        model="deepseek-v4-flash",
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
