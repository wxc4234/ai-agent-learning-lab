"""工作空间子路由共用的严格路径与正文类型。"""

from typing import Annotated

from fastapi import Path
from pydantic import BaseModel, ConfigDict


class DirectorySelectionRequest(BaseModel):
    """选择行为不接受浏览器提供路径、命令或身份字段。"""

    model_config = ConfigDict(extra="forbid")


TaskIdentifier = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


ProposalDecisionIdentifier = Annotated[
    str,
    Path(
        min_length=32,
        max_length=32,
        pattern=r"^[0-9a-f]{32}$",
    ),
]
