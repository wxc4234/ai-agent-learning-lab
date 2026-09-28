"""仅组装工作空间领域路由；安全边界由各子路由统一复用。"""

from fastapi import APIRouter

from app.routers.workspace import (
    directories,
    execution,
    projects,
    proposals,
    project_write_grants,
    samples,
    tasks,
)

router = APIRouter()
for domain in (projects, directories, tasks, proposals, execution, samples, project_write_grants):
    router.include_router(domain.router)
