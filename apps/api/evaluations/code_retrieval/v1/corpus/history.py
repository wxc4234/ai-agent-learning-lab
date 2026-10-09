def save_turn(history, question, answer, status):
    """Persist a completed conversation turn; skip an aborted run."""
    if status == "done":
        history.extend([("user", question), ("assistant", answer)])
    return history


def restore_history(history):
    """Read saved messages without replaying tools or model requests."""
    return list(history)
