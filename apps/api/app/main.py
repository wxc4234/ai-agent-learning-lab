import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.local_boundary import local_access_boundary

from app.config import settings
from app.services.runtime.execution.execution_budget import ExecutionBudget
from app.database import check_database_ready
from app.routers.chat.chat import router as chat_router
from app.routers.chat.conversation import router as conversation_router
from app.routers.system.health import router as health_router
from app.routers.runtime.runs import router as runs_router
from app.routers.runtime.tools import router as tools_router
from app.routers.auth.registration import router as registration_router
from app.routers.auth.login import router as login_router
from app.routers.auth.current_user import router as current_user_router
from app.routers.auth.logout import router as logout_router
from app.routers.workspace.router import router as workspace_router
from app.services.runtime.execution.run_cancellation import close_cancellation_broker
from app.services.runtime.execution.command_recovery_store import (
    CommandRecoveryStore,
)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    # 数据库准备完成后再创建应用级资源，每次启动使用独立实例。
    await asyncio.to_thread(check_database_ready)
    application.state.execution_budget = ExecutionBudget(
        capacity=settings.agent_max_concurrent_executions,
    )
    application.state.command_recovery_store = CommandRecoveryStore()

    try:
        yield
    finally:
        try:
            await close_cancellation_broker()
        finally:
            # 本课仅内存保存，应用退出不等于容器已经清理。
            # 这里释放Python引用，不执行猜测性Docker删除。
            del application.state.command_recovery_store
            del application.state.execution_budget


# 将生命周期交给 FastAPI，确保启动和关闭资源的时机集中管理。
app = FastAPI(lifespan=lifespan)
app.middleware("http")(local_access_boundary)

# 入口不实现具体业务；各业务接口由独立 router 按领域注册。
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(conversation_router)
app.include_router(tools_router)
app.include_router(runs_router)
app.include_router(registration_router)
app.include_router(login_router)
app.include_router(current_user_router)
app.include_router(logout_router)
app.include_router(workspace_router)
"""FastAPI 应用装配入口：只负责生命周期和路由注册。"""
