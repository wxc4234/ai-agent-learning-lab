from fastapi import APIRouter, HTTPException

from app.repositories.conversation_repository import load_conversation
from app.schemas import ConversationHistoryResponse

# 该接口服务于后续的会话列表、刷新恢复和 Agent 运行历史界面。
router = APIRouter(tags=["conversations"])


@router.get(
    "/sessions/{session_id}/messages", response_model=ConversationHistoryResponse
)
def get_session_message(session_id: str):
    messages = load_conversation(session_id)

    if not messages:
        # 用 404 区分“会话不存在”和“服务或数据库出错”。
        raise HTTPException(
            status_code=404,
            detail="该会话不存在",
        )

    return {
        "session_id": session_id,
        "total": len(messages),
        "messages": messages,
    }


"""会话历史查询接口。"""
