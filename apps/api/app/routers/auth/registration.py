"""注册 HTTP 接口及局部错误响应边界。"""

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import SessionLocal
from app.schemas import (
    RegisterRequest,
    RegisterResponse,
    RegistrationErrorResponse,
)
from app.services.auth.registration_service import (
    UsernameAlreadyExistsError,
    register_user,
)

logger = logging.getLogger(__name__)


def _error_response(
    status_code: int,
    *,
    code: str,
    message: str,
) -> JSONResponse:
    """显式构建安全响应，不序列化原始异常。"""

    payload = RegistrationErrorResponse(code=code, message=message)

    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
    )


class RegistrationRoute(APIRoute):
    """在注册请求校验、执行和响应生成外层处理错误。"""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await original_handler(request)
            except RequestValidationError:
                return _error_response(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    code="invalid_registration_input",
                    message="注册信息不符合要求，请检查用户名和密码",
                )
            except UsernameAlreadyExistsError:
                return _error_response(
                    status.HTTP_409_CONFLICT,
                    code=UsernameAlreadyExistsError.code,
                    message="用户名已被使用",
                )
            except StarletteHTTPException as error:
                return _error_response(
                    error.status_code, code="request_rejected", message="请求无法处理"
                )
            except Exception:  # noqa: BLE001 -- HTTP 边界统一脱敏未知错误
                # 只记录固定事件，不记录异常、请求体或 SQL 参数。
                logger.error("registration_failed")
                return _error_response(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="registration_failed",
                    message="注册失败，请稍候再试",
                )

        return safe_handler


router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    route_class=RegistrationRoute,
)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=RegisterResponse,
    responses={
        400: {
            "model": RegistrationErrorResponse,
            "description": "请求无法解析",
        },
        409: {
            "model": RegistrationErrorResponse,
            "description": "用户名已被使用",
        },
        422: {
            "model": RegistrationErrorResponse,
            "description": "注册信息校验失败",
        },
        500: {"model": RegistrationErrorResponse, "description": "注册失败"},
    },
)
def register(payload: RegisterRequest) -> RegisterResponse:
    """创建账号；注册服务拥有事务，路由负责关闭 Session。"""
    with SessionLocal() as session:
        result = register_user(session, payload)

    return RegisterResponse(external_id=result.external_id, username=result.username)
