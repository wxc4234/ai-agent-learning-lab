import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.repositories.conversation_repository import init_db
from app.routers.chat import router as chat_router
from app.routers.conversation import router as conversation_router
from app.routers.health import router as health_router
from app.routers.runs import router as runs_router
from app.routers.tools import router as tools_router
from app.routers.registration import router as registration_router
from app.services.run_cancellation import close_cancellation_broker


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

# 入口不实现具体业务；各业务接口由独立 router 按领域注册。
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(conversation_router)
app.include_router(tools_router)
app.include_router(runs_router)
app.include_router(registration_router)
"""FastAPI 应用装配入口：只负责生命周期和路由注册。"""
