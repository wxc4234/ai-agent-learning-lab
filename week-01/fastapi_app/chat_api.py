import os
from database import init_db
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import AsyncOpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam

load_dotenv()

app = FastAPI()

init_db()

client = AsyncOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

conversations: dict[
    str,
    list[ChatCompletionMessageParam]
] = {}

MAX_ROUNDS = 5

class ChatRequest(BaseModel):
    session_id: str
    prompt: str

class ChatResponse(BaseModel):
    reply: str

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    if request.session_id not in conversations:
        conversations[request.session_id] = [
            {
                "role": "system",
                "content": "你是一个贴心并且简洁的 AI 助手。"
            }
        ]

    history = conversations[request.session_id]
    history.append({
        "role": "user",
        "content": request.prompt
    })

    recent_history = history[1:-1][-(MAX_ROUNDS * 2):]
    messages_to_send: list[ChatCompletionMessageParam] = [
        history[0],
        *recent_history,
        history[-1],
    ]

    try:
        response = await client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=messages_to_send,
                stream=False
            )
        reply = response.choices[0].message.content or ""
        history.append({
            "role": "assistant",
            "content": reply
        })
        max_saved_messages = MAX_ROUNDS * 2
        if len(history) > max_saved_messages + 1:
            del(history[1:-max_saved_messages])

        return {
            "reply": reply
        }
    except OpenAIError:
        history.pop()
        raise HTTPException(
            status_code=502,
            detail="模型服务暂时不可用"
        )
