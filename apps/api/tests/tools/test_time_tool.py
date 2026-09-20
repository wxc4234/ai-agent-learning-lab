from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.tools.registry import (
    REGISTERED_TOOLS,
    TOOL_REGISTRY,
    TOOLS,
    ToolDefinition,
    CalculateRectangleAreaArguments,
    calculate_rectangle_area,
    GetCurrentTimeArguments,
    get_current_time,
)


def test_get_current_time_uses_requested_utc_offset():
    value = get_current_time(utc_offset_hours=8)
    parsed_time = datetime.fromisoformat(value)

    assert parsed_time.utcoffset() == timedelta(hours=8)


def test_time_tool_is_explicitly_registered():
    tool_names = {tool["function"]["name"] for tool in TOOLS}
    tool_definition = TOOL_REGISTRY["get_current_time"]

    assert "get_current_time" in tool_names
    assert tool_definition.executor is get_current_time
    assert tool_definition.arguments_model is GetCurrentTimeArguments


def test_time_tool_schema_describes_required_parameter():
    tool = next(
        tool for tool in TOOLS if tool["function"]["name"] == "get_current_time"
    )
    parameters = tool["function"].get("parameters")
    assert parameters is not None

    properties = parameters["properties"]
    assert isinstance(properties, dict)

    utc_offset_schema = properties["utc_offset_hours"]
    assert isinstance(utc_offset_schema, dict)

    assert parameters["required"] == ["utc_offset_hours"]
    assert parameters["additionalProperties"] is False
    assert utc_offset_schema["minimum"] == -12
    assert utc_offset_schema["maximum"] == 14


@pytest.mark.parametrize(
    ("raw_arguments", "expected_error_type"),
    [
        ('{"utc_offset_hours": 15}', "less_than_equal"),
        ('{"utc_offset_hours": "8"}', "int_type"),
        (
            '{"utc_offset_hours": 8, "command": "read-secret"}',
            "extra_forbidden",
        ),
    ],
)
def test_time_tool_rejects_invalid_arguments(
    raw_arguments: str,
    expected_error_type: str,
):
    with pytest.raises(ValidationError) as error_info:
        GetCurrentTimeArguments.model_validate_json(raw_arguments)

    assert error_info.value.errors()[0]["type"] == expected_error_type


def test_tool_definition_validates_and_executes_arguments():
    definition = ToolDefinition(
        name="get_current_time",
        description="获取指定 UTC 时差下的当前日期和时间",
        arguments_model=GetCurrentTimeArguments,
        executor=get_current_time,
    )

    model_tool = definition.as_model_tool()
    function_definition = model_tool["function"]
    parameters = function_definition.get("parameters")

    assert function_definition["name"] == "get_current_time"
    assert parameters == GetCurrentTimeArguments.model_json_schema()

    validated_arguments = definition.validate_arguments('{"utc_offset_hours": 8}')
    result = definition.execute(validated_arguments)
    parsed_time = datetime.fromisoformat(result)

    assert parsed_time.utcoffset() == timedelta(hours=8)


def test_tool_registry_and_model_tools_are_derived_from_registered_tools():
    assert TOOLS == [
        tool.as_model_tool() for tool in REGISTERED_TOOLS if not tool.requires_context
    ]
    assert TOOL_REGISTRY == {tool.name: tool for tool in REGISTERED_TOOLS}


def test_rectangle_area_tool_is_registered_and_executable():
    tool_definition = TOOL_REGISTRY["calculate_rectangle_area"]

    assert tool_definition.arguments_model is CalculateRectangleAreaArguments
    assert tool_definition.executor is calculate_rectangle_area

    validated_arguments = tool_definition.validate_arguments(
        '{"width": 3, "height": 4}'
    )
    result = tool_definition.execute(validated_arguments)

    assert result == "12"
