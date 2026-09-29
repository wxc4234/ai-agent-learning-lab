"""仅组装工作空间领域路由；安全边界由各子路由统一复用。"""

from fastapi import APIRouter

from app.routers.workspace import (
    directories,
    change_sets,
    owned_areas,
    git_staged,
    execution,
    projects,
    proposals,
    project_write_grants,
    samples,
    tasks,
)

router = APIRouter()
for domain in (projects, change_sets, owned_areas, directories, tasks, proposals, execution, samples, project_write_grants, git_staged):
    router.include_router(domain.router)
