"""仅本地BFF可读写模型配置；错误响应不带输入或密钥。"""

import asyncio
import httpx

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.local_boundary import local_request_allowed
from app.services.model.local_model_settings import Channel, Update, detect_dimensions, public_settings, save_settings

router = APIRouter(prefix="/model-settings", tags=["model-settings"])


@router.api_route("", methods=["GET", "PUT"])
@router.post("/detect-dimensions")
async def model_settings(request: Request):
    def response(body, status=200):
        return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})

    if settings.app_mode != "local" or not local_request_allowed(request):
        return response({"message": "仅本机可配置模型"}, 403)
    try:
        if request.method == "GET":
            return response(await asyncio.to_thread(public_settings))
        if request.headers.get("origin") not in settings.login_allowed_origins:
            return response({"message": "请求来源不被允许"}, 403)
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return response({"message": "需要JSON请求"}, 415)
        content = bytearray()
        async for part in request.stream():
            content.extend(part)
            if len(content) > 16384:
                return response({"message": "配置内容过长"}, 413)
        if request.url.path.endswith("/detect-dimensions"):
            return response({"dimensions": await detect_dimensions(Channel.model_validate_json(bytes(content)))})
        update = Update.model_validate_json(bytes(content))
        return response(await asyncio.to_thread(save_settings, update))
    except ValueError as exc:
        reason = str(exc)
        if reason == "model_settings_conflict":
            return response({"message": "配置已被修改，请重新打开设置后再保存"}, 409)
        if reason == "embedding_provider_unsupported":
            return response({"message": "DeepSeek此地址用于聊天，请为代码检索选择支持Embedding的服务"}, 422)
        return response({"message": "配置无效，请检查地址、模型、密钥和向量维度"}, 422)
    except (httpx.HTTPError, TimeoutError):
        return response({"message": "维度检测失败，请检查Embedding服务地址、模型和密钥"}, 502)
    except OSError:
        return response({"message": "本机配置读写失败，未确认保存"}, 503)
