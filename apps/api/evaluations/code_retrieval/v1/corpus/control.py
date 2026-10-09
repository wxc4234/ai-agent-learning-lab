def cancel_task(state):
    """Request cancellation; running work stops at its next checkpoint."""
    state["cancel_requested"] = True
    return state


def cancellation_checkpoint(state):
    """Stop before another tool when cancellation was requested."""
    if state.get("cancel_requested"):
        return "aborted"
    return "running"
