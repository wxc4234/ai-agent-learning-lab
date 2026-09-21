from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openai import OpenAIError

from app.schemas import ChatResponse
from app.services.chat.chat_service import create_chat_reply, stream_chat_reply
from app.routers.chat.chat_execution import CurrentChatExecution
from app.routers.chat.chat_boundary import ChatRoute

# tag 只影响 Swagger 分组，让前端联调时按业务而非文件查找接口。
router = APIRouter(
    tags=["chat"],
    route_class=ChatRoute,
)


@router.get("/chat")
def chat_help():
    return {
        "message": "聊天接口需要使用 POST 请求",
        "docs": "/docs",
    }


@router.post(
    "/chat",
    response_model=ChatResponse,
)
async def chat(
    execution: CurrentChatExecution,
) -> ChatResponse:
    try:
        reply = await create_chat_reply(
            user_id=execution.user_id,
            session_id=execution.body.session_id,
            prompt=execution.body.prompt,
            execution_threads=execution.threads,
        )
    except OpenAIError as error:
        raise HTTPException(
            status_code=502,
            detail="模型服务暂时不可用",
        ) from error

    return ChatResponse(reply=reply)


"""聊天相关 HTTP 接口；业务编排位于 services.chat_service。"""


@router.post("/chat/stream")
async def chat_stream(
    execution: CurrentChatExecution,
) -> StreamingResponse:
    # request 级依赖已取得占用，忙请求不会创建运行。
    run_id = await execution.start_run()
    execution.stream = stream_chat_reply(
        user_id=execution.user_id,
        session_id=execution.body.session_id,
        prompt=execution.body.prompt,
        run_id=run_id,
        execution_threads=execution.threads,
        monitors=execution.monitors,
        # 绑定到当前请求；Run身份与恢复存储由ChatExecution持有。
        command_executor=execution.execute_command,
    )
    return StreamingResponse(
        execution.stream,
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            "X-Run-ID": str(run_id),
        },
    )
