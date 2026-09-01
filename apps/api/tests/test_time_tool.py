import re

from app.tools.registry import TOOL_FUNCTIONS, TOOLS, get_current_time


def test_get_current_time_returns_expected_format():
    value = get_current_time()

    assert isinstance(value, str)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value)


def test_time_tool_is_explicitly_registered():
    tool_names = {tool["function"]["name"] for tool in TOOLS}

    assert "get_current_time" in tool_names
    assert TOOL_FUNCTIONS["get_current_time"] is get_current_time
