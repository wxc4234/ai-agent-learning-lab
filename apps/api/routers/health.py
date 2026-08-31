from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/")
def home():
    return {
        "status": "ok",
        "message": "AI 对话服务已启动",
        "docs": "/docs",
    }
"""服务健康检查接口，供浏览器、部署平台和开发调试使用。"""
