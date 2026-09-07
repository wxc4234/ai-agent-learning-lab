"""工具描述、参数模型与 Python 执行白名单。"""

from dataclasses import dataclass
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from openai.types.chat import ChatCompletionToolParam
from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """一个工具的模型描述、参数校验器与执行函数。"""

    name: str
    description: str
    arguments_model: type[BaseModel]
    executor: Callable[..., str]
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")

    def as_model_tool(self) -> ChatCompletionToolParam:
        """生成提供给模型的 Function Calling 定义。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments_model.model_json_schema(),
            },
        }

    def validate_arguments(self, raw_arguments: str) -> BaseModel:
        """把模型返回的 JSON 字符串校验成参数对象。"""
        return self.arguments_model.model_validate_json(raw_arguments)

    def execute(self, arguments: BaseModel) -> str:
        """只使用已经校验过的参数执行工具。"""
        return self.executor(**arguments.model_dump())


class GetCurrentTimeArguments(BaseModel):
    """获取指定 UTC 时差下的当前时间。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    utc_offset_hours: int = Field(
        ge=-12,
        le=14,
        description="目标时区相对 UTC 的整数小时偏移，例如北京时间使用 8",
    )


def get_current_time(*, utc_offset_hours: int) -> str:
    """返回指定 UTC 时差下的 ISO 8601 时间。"""
    target_timezone = timezone(timedelta(hours=utc_offset_hours))
    current_time = datetime.now(target_timezone)
    return current_time.isoformat(timespec="seconds")


class CalculateRectangleAreaArguments(BaseModel):
    """计算矩形面积所需的参数。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    width: float = Field(
        gt=0,
        le=10_000,
        description="矩形宽度，必须大于 0",
    )
    height: float = Field(
        gt=0,
        le=10_000,
        description="矩形高度，必须大于 0",
    )


def calculate_rectangle_area(*, width: float, height: float) -> str:
    """计算矩形面积并返回字符串结果。"""
    return f"{width * height:g}"


# 新增工具时，只需在这个元组中增加一个 ToolDefinition。
REGISTERED_TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="get_current_time",
        description="获取指定 UTC 时差下的当前日期和时间",
        arguments_model=GetCurrentTimeArguments,
        executor=get_current_time,
    ),
    ToolDefinition(
        name="calculate_rectangle_area",
        description="根据宽度和高度计算矩形面积",
        arguments_model=CalculateRectangleAreaArguments,
        executor=calculate_rectangle_area,
    ),
)


# 以下三个兼容接口都从唯一注册源派生。
TOOLS: list[ChatCompletionToolParam] = [
    tool.as_model_tool() for tool in REGISTERED_TOOLS
]

TOOL_REGISTRY: dict[str, ToolDefinition] = {
    tool.name: tool for tool in REGISTERED_TOOLS
}
"""工具描述与 Python 执行白名单。模型只能调用这里显式注册的工具。"""
