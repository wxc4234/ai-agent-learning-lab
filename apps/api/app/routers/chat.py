from fastapi import APIRouter, HTTPException
from openai import OpenAIError

from app.schemas import ChatRequest, ChatResponse
from app.services.chat_service import create_chat_reply

# tag 只影响 Swagger 分组，让前端联调时按业务而非文件查找接口。
router = APIRouter(tags=["chat"])


@router.get("/chat")
def chat_help():
    return {
        "message": "聊天接口需要使用 POST 请求",
        "docs": "/docs",
    }


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        # Router 只转换 HTTP 请求；记忆、模型调用和持久化由 Service 统一处理。
        reply = await create_chat_reply(
            session_id=request.session_id,
            prompt=request.prompt,
        )
    except OpenAIError as error:
        # 不向前端暴露供应商底层异常，统一转换成可理解的网关错误。
        raise HTTPException(
            status_code=502,
            detail="模型服务暂时不可用",
        ) from error

    return ChatResponse(reply=reply)
"""聊天相关 HTTP 接口；业务编排位于 services.chat_service。"""
