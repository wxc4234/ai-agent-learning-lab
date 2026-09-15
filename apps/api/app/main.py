import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.local_boundary import local_access_boundary

from app.repositories.chat.conversation_repository import init_db
from app.routers.chat.chat import router as chat_router
from app.routers.chat.conversation import router as conversation_router
from app.routers.system.health import router as health_router
from app.routers.runtime.runs import router as runs_router
from app.routers.runtime.tools import router as tools_router
from app.routers.auth.registration import router as registration_router
from app.routers.auth.login import router as login_router
from app.routers.auth.current_user import router as current_user_router
from app.routers.auth.logout import router as logout_router
from app.routers.workspace.workspace import router as workspace_router
from app.services.runtime.run_cancellation import close_cancellation_broker


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 数据库只应在服务启动时初始化，避免测试或脚本导入 main.py 时产生副作用。
    await asyncio.to_thread(init_db)

    try:
        yield
    finally:
        await close_cancellation_broker()


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
