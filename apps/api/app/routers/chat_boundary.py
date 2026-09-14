"""聊天 HTTP 边界：来源校验，以及流开始前的安全错误响应。"""

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.config import settings
from app.services.login_session_resolver import InvalidLoginSessionError
from app.repositories.conversation_repository import (
    ConversationNotAccessibleError,
)


logger = logging.getLogger(__name__)


def _error_response(
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message},
        headers={"Cache-Control": "no-store"},
    )


class ChatRoute(APIRoute):
    """处理依赖求解与接口执行期间、响应流开始之前的错误。"""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                if request.method == "POST":
                    origin = request.headers.get("origin")

                    if origin not in settings.login_allowed_origins:
                        return _error_response(
                            403,
                            "chat_origin_rejected",
                            "聊天请求来源不被允许",
                        )

                    content_type = (
                        request.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )

                    if content_type != "application/json":
                        return _error_response(
                            415,
                            "unsupported_chat_content_type",
                            "聊天请求必须使用 application/json",
                        )

                response = await original_handler(request)
                response.headers["Cache-Control"] = "no-store"
                return response

            except InvalidLoginSessionError:
                return _error_response(
                    401,
                    "invalid_login_session",
                    "登录状态无效，请重新登录",
                )

            except ConversationNotAccessibleError:
                return _error_response(
                    404,
                    "conversation_not_accessible",
                    "会话不存在或不可访问",
                )

            except RequestValidationError:
                return _error_response(
                    422,
                    "invalid_chat_input",
                    "聊天信息不符合要求",
                )

            except HTTPException as error:
                if error.status_code == 502:
                    return _error_response(
                        502,
                        "model_service_unavailable",
                        "模型服务暂时不可用",
                    )

                if error.status_code == 400:
                    return _error_response(
                        400,
                        "invalid_chat_request",
                        "聊天请求无法解析",
                    )

                logger.error("chat_http_failed")
                return _error_response(
                    500,
                    "chat_failed",
                    "聊天服务暂时出错",
                )

            except Exception:  # noqa: BLE001 -- HTTP 边界统一脱敏
                logger.error("chat_failed")
                return _error_response(
                    500,
                    "chat_failed",
                    "聊天服务暂时出错",
                )

        return safe_handler