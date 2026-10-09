import json


def measure_request_bytes(messages, tools):
    """Measure the complete model request as UTF-8 bytes, not tokens."""
    return len(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False).encode("utf-8"))


def check_request_budget(size, limit):
    """Reject an oversized prompt before the model request."""
    if size > limit:
        raise ValueError("request_too_large")
    return True
