"""Task 模型关系与兼容字段契约。"""

from sqlalchemy import inspect
from sqlalchemy.orm import configure_mappers

from app.models import Conversation, Task, Workspace


def test_task_relationships_pair_with_the_correct_models():
    # 主动完成 mapper 配置，提前暴露关系写错类或 back_populates 不配对。
    configure_mappers()
    workspace = inspect(Workspace)
    task = inspect(Task)
    conversation = inspect(Conversation)
    assert "task" not in workspace.relationships
    assert workspace.relationships.tasks.mapper.class_ is Task
    assert task.relationships.workspace.mapper.class_ is Workspace
    assert task.relationships.conversation.mapper.class_ is Conversation
    assert conversation.relationships.task.mapper.class_ is Task
    assert not task.relationships.conversation.uselist
    assert not conversation.relationships.task.uselist


def test_legacy_conversation_can_omit_task_but_task_requires_workspace():
    column = Conversation.__table__.c.task_id
    assert column.nullable
    assert column.default is None
    assert column.server_default is None
    assert {key.target_fullname for key in column.foreign_keys} == {"tasks.id"}
    assert any(index.unique and list(index.columns.keys()) == ["task_id"] for index in Conversation.__table__.indexes)
    assert not Task.__table__.c.workspace_id.nullable
    assert {key.target_fullname for key in Task.__table__.c.workspace_id.foreign_keys} == {"workspaces.id"}
