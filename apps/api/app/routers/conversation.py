from fastapi import APIRouter

from app.repositories.conversation_repository import load_conversation
from app.schemas import ConversationHistoryResponse, ConversationMessage
from app.dependencies import CurrentUser
from app.routers.chat_boundary import ChatRoute

# 该接口服务于后续的会话列表、刷新恢复和 Agent 运行历史界面。
router = APIRouter(
    tags=["conversations"],
    route_class=ChatRoute,
)


@router.get(
    "/sessions/{session_id}/messages",
    response_model=ConversationHistoryResponse,
)
def get_session_message(
    session_id: str,
    current_user: CurrentUser,
) -> ConversationHistoryResponse:
    messages = load_conversation(
        user_id=current_user.id,
        session_id=session_id,
    )

    return ConversationHistoryResponse(
        session_id=session_id,
        total=len(messages),
        messages=[ConversationMessage.model_validate(message) for message in messages],
    )

"""会话历史查询接口。"""
