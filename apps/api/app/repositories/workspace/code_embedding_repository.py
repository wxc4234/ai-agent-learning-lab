"""受控向量批次的存取；授权读与插入都由调用方管理事务。"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import (
    CodeEmbeddingBatch,
    CodeEmbeddingSpace,
    CodeEmbeddingVector,
    Conversation,
    Task,
    Workspace,
)
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError


def insert_code_embedding_batch(
    session: Session,
    *,
    task_pk: int,
    bound_root: str,
    binding_revision: int,
    space_id: str,
    dimensions: int,
    requested_model: str,
    response_model: str,
    metadata: dict,
    vectors: list[dict],
) -> CodeEmbeddingBatch:
    """只接受服务端已验证、已重新授权并持有归属锁的值；不单独commit。"""
    # 不同项目可同时首次使用相同空间；唯一键竞争交给PostgreSQL处理。
    session.execute(
        insert(CodeEmbeddingSpace)
        .values(
            id=space_id,
            dimensions=dimensions,
            requested_model=requested_model,
            response_model=response_model,
        )
        .on_conflict_do_nothing(index_elements=[CodeEmbeddingSpace.id])
    )
    space = session.get(CodeEmbeddingSpace, space_id, populate_existing=True)
    if space is None or (
        space.dimensions,
        space.requested_model,
        space.response_model,
    ) != (dimensions, requested_model, response_model):
        raise ValueError("code_embedding_space_conflict")

    batch = CodeEmbeddingBatch(
        external_id=uuid4().hex,
        task_id=task_pk,
        bound_root=bound_root,
        binding_revision=binding_revision,
        space_id=space_id,
        dimensions=dimensions,
        chunk_count=len(vectors),
        source_metadata=metadata,
    )
    session.add(batch)
    session.flush()
    session.add_all(
        [
            CodeEmbeddingVector(
                batch_id=batch.id,
                space_id=space_id,
                dimensions=dimensions,
                ordinal=ordinal,
                **values,
            )
            for ordinal, values in enumerate(vectors)
        ]
    )
    # 批次、模型空间和全部向量处于同一外层事务；任一步失败整体回滚。
    session.flush()
    return batch


def read_owned_code_embedding_batch(
    session: Session,
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    batch_id: str,
    space_id: str,
) -> CodeEmbeddingBatch:
    """编号/空间均不是授权；当前归属、任务范围和目录绑定必须同时匹配。"""
    statement = (
        select(CodeEmbeddingBatch)
        .join(Task, CodeEmbeddingBatch.task_id == Task.id)
        .join(Workspace, Task.workspace_id == Workspace.id)
        .join(Conversation, Conversation.task_id == Task.id)
        .where(
            Workspace.user_id == user_id,
            Workspace.external_id == workspace_id,
            Task.external_id == task_id,
            Conversation.user_id == user_id,
            CodeEmbeddingBatch.external_id == batch_id,
            CodeEmbeddingBatch.space_id == space_id,
            CodeEmbeddingBatch.binding_revision == Workspace.binding_revision,
            CodeEmbeddingBatch.bound_root == Workspace.root_path,
        )
        .execution_options(populate_existing=True)
    )
    with session.no_autoflush:
        batch = session.scalar(statement)
    if batch is None:
        raise WorkspaceNotAccessibleError()
    return batch


def read_batch_vectors(
    session: Session, batch: CodeEmbeddingBatch
) -> list[CodeEmbeddingVector]:
    """仅供已授权批次读取使用；限制结果大小，不执行相似度检索。"""
    return list(
        session.scalars(
            select(CodeEmbeddingVector)
            .where(
                CodeEmbeddingVector.batch_id == batch.id,
                CodeEmbeddingVector.space_id == batch.space_id,
                CodeEmbeddingVector.dimensions == batch.dimensions,
            )
            .order_by(CodeEmbeddingVector.ordinal)
            .limit(21)
        )
    )
