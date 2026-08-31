import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from repositories.conversation_repository import init_db
from routers.chat import router as chat_router
from routers.conversation import router as conversation_router
from routers.health import router as health_router
from routers.tools import router as tools_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 数据库只应在服务启动时初始化，避免测试或脚本导入 main.py 时产生副作用。
    await asyncio.to_thread(init_db)

    # yield 前是启动阶段；后续 PostgreSQL、Redis 等资源的关闭逻辑会放在 yield 后。
    yield


# 将生命周期交给 FastAPI，确保启动和关闭资源的时机集中管理。
app = FastAPI(lifespan=lifespan)

# 入口不实现具体业务；各业务接口由独立 router 按领域注册。
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(conversation_router)
app.include_router(tools_router)
"""FastAPI 应用装配入口：只负责生命周期和路由注册。"""
