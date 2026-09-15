"""当前用户 HTTP 接口：读取 Cookie，解析身份并返回安全响应。"""

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.dependencies import CurrentUser
from app.schemas import CurrentUserErrorResponse, CurrentUserResponse
from app.services.auth.login_session_resolver import InvalidLoginSessionError


logger = logging.getLogger(__name__)


def _error_response(
    status_code: int,
    *,
    code: str,
    message: str,
) -> JSONResponse:
    """不序列化原始异常或 Cookie 内容。"""

    payload = CurrentUserErrorResponse(code=code, message=message)

    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
        headers={"Cache-Control": "no-store"},
    )

class CurrentUserRoute(APIRoute):
    """覆盖当前用户查询和响应生成的局部错误边界。"""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                response = await original_handler(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except InvalidLoginSessionError:
                return _error_response(
                    status.HTTP_401_UNAUTHORIZED,
                    code=InvalidLoginSessionError.code,
                    message="登录状态无效，请重新登录",
                )
            except Exception:  # noqa: BLE001 -- HTTP 边界统一脱敏
                logger.error("current_user_failed")
                return _error_response(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="current_user_failed",
                    message="获取当前用户失败，请稍候再试",
                )

        return safe_handler

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    route_class=CurrentUserRoute,
)

@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=CurrentUserResponse,
    responses={
        401: {
            "model": CurrentUserErrorResponse,
            "description": "登录状态无效",
        },
        500: {
            "model": CurrentUserErrorResponse,
            "description": "获取当前用户失败",
        },
    },
)
def get_current_user(identity: CurrentUser) -> CurrentUserResponse:
    """依赖负责提供登录身份；接口只组装公开响应。"""

    return CurrentUserResponse(
        external_id=identity.external_id,
        username=identity.username,
    )