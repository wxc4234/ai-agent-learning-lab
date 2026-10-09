"""在当前授权摘要窗口内选择兼容批次；选择结果不是后续发送许可。"""

from dataclasses import dataclass
from typing import Literal

from app.services.model.code_embeddings import code_embedding_space_id
from app.services.model.embedding_config import EmbeddingConfig
from app.services.workspace.files.code_batch_summaries import (
    CodeEmbeddingBatchSummary,
    list_code_embedding_batches,
)


class CodeBatchSelectionError(ValueError):
    """配置失败使用固定分类，不公开密钥、供应商地址或原始异常。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CodeBatchSelectionResult:
    # 目标只标识这次读取的快照，不保存或传递授权凭证。
    workspace_id: str
    task_id: str

    # 未找到是正常选择结果；授权或读取失败仍然抛出异常。
    status: Literal["selected", "not_found_in_window"]
    selected: CodeEmbeddingBatchSummary | None

    # 表示整个返回窗口的候选数量，不是匹配数量或历史总数。
    candidate_count: int
    has_more: bool
    limit: Literal[20] = 20
    source: Literal["code_embedding_batch_selection"] = (
        "code_embedding_batch_selection"
    )


def _prepare_config(config: EmbeddingConfig) -> EmbeddingConfig:
    """重新校验配置副本，避免绕过构造校验的对象进入选择流程。"""

    try:
        if not isinstance(config, EmbeddingConfig):
            raise TypeError("invalid_config_type")

        # 从字段重新构造，确保执行字段校验，而不是直接信任模型实例。
        # mode="python" 保留 SecretStr；禁止序列化警告反射非法原值。
        # 后续 model_validate 仍严格拒绝坏字段，不因关闭警告而放宽校验。
        return EmbeddingConfig.model_validate(
            config.model_dump(mode="python", warnings=False)
        )
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise CodeBatchSelectionError(
            "invalid_code_embedding_batch_selection_config"
        ) from None


def select_code_embedding_batch(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    config: EmbeddingConfig,
) -> CodeBatchSelectionResult:
    """由可信宿主提供当前目标与独立配置，选择窗口内最近的兼容批次。"""

    # 无效配置在摘要服务开启数据库事务前拒绝。
    active = _prepare_config(config)

    # 身份、目标、当前归属与目录绑定由现有摘要服务统一核对。
    # 授权、元数据或事务退出失败直接传播，不能改成“没有候选”。
    summaries = list_code_embedding_batches(
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
    )

    # 事务边界：摘要服务成功返回时，其数据库事务已经退出。
    # 此后只比较有界内存摘要，不访问磁盘、向量或供应商。
    selected: CodeEmbeddingBatchSummary | None = None

    # 摘要已经按创建时间、批次 ID 降序排列，不重新排序或跨窗查找。
    for batch in summaries.batches:
        if (
            batch.requested_model != active.model
            or batch.dimensions != active.dimensions
        ):
            continue

        # 每批使用自己的报告版本；不能拿某一个版本套用整个窗口。
        expected_space_id = code_embedding_space_id(
            active,
            batch.response_model,
        )
        if batch.space_id != expected_space_id:
            continue

        # 返回独立摘要，保留生成时覆盖事实；不携带配置或绑定路径。
        selected = batch.model_copy(deep=True)
        break

    status: Literal["selected", "not_found_in_window"]
    if selected is None:
        status = "not_found_in_window"
    else:
        status = "selected"

    return CodeBatchSelectionResult(
        workspace_id=summaries.workspace_id,
        task_id=summaries.task_id,
        status=status,
        selected=selected,
        candidate_count=len(summaries.batches),
        has_more=summaries.has_more,
        limit=summaries.limit,
    )
