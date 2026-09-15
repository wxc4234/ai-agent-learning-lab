"""运行接口的来源校验与安全错误边界。"""

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from redis.exceptions import RedisError

from app.config import settings
from app.repositories.runtime.run_repository import RunNotAccessibleError
from app.services.auth.login_session_resolver import InvalidLoginSessionError


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


class RunRoute(APIRoute):
    """统一处理运行查询与取消的 HTTP 边界。"""

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
                            "run_origin_rejected",
                            "运行请求来源不被允许",
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
                            "unsupported_run_content_type",
                            "运行请求必须使用 application/json",
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

            except RunNotAccessibleError:
                return _error_response(
                    404,
                    "run_not_accessible",
                    "运行不存在或不可访问",
                )

            except RequestValidationError:
                return _error_response(
                    422,
                    "invalid_run_input",
                    "运行请求信息不符合要求",
                )

            except RedisError:
                logger.error("run_cancellation_broker_failed")
                return _error_response(
                    503,
                    "run_cancellation_unavailable",
                    "取消服务暂时不可用",
                )

            except HTTPException as error:
                if error.status_code == 400:
                    return _error_response(
                        400,
                        "invalid_run_request",
                        "运行请求无法解析",
                    )

                logger.error("run_http_failed")
                return _error_response(
                    500,
                    "run_failed",
                    "运行服务暂时出错",
                )

            except Exception:  # noqa: BLE001 -- HTTP 边界统一脱敏
                logger.error("run_failed")
                return _error_response(
                    500,
                    "run_failed",
                    "运行服务暂时出错",
                )

        return safe_handler
