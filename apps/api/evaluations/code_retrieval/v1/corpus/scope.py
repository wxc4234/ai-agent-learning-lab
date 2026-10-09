def authorize_workspace(owner_id, user_id):
    """Reject access when the workspace owner differs from the caller."""
    if owner_id != user_id:
        raise PermissionError("workspace_not_accessible")
    return True


def verify_revision(expected_revision, current_revision):
    """Reject a changed directory binding before sending code."""
    if expected_revision != current_revision:
        raise ValueError("binding_changed")
    return True
