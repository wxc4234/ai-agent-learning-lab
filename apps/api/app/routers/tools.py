"""Tool Calling 演示接口，通过通用 Agent Runtime 完成多步骤决策。"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from openai import OpenAIError

from app.config import settings
from app.schemas import ToolTestRequest
from app.services.agent_runtime import AgentObservation, run_agent_loop
from app.services.model_client import client
from app.services.model_decision import (
    DEFAULT_SYSTEM_PROMPT,
    DeepSeekDecisionMaker,
    ModelDecisionError,
)

router = APIRouter(tags=["tools"])


def _serialize_observation(observation: AgentObservation) -> dict[str, object]:
    """保持 /tool-test 原有响应契约，不暴露 Runtime 内部计时字段。"""

    return {
        key: value for key, value in asdict(observation).items() if key != "duration_ms"
    }


@router.post("/tool-test")
async def tool_test(request: ToolTestRequest):
    decision_maker = DeepSeekDecisionMaker(
        client=client,
        model=settings.deepseek_model,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        user_prompt=request.prompt,
    )

    try:
        result = await run_agent_loop(decision_maker, max_steps=5)
    except OpenAIError as error:
        raise HTTPException(
            status_code=502,
            detail="模型服务暂时不可用",
        ) from error
    except ModelDecisionError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

    return {
        "type": (
            "final_answer" if result.status == "completed" else "max_steps_exceeded"
        ),
        "reply": result.answer,
        "steps_taken": result.steps_taken,
        "observations": [
            _serialize_observation(observation) for observation in result.observations
        ],
    }
