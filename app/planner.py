MANAGED_FIELDS = ("ipv4", "description", "admin_state")

def _plan_result(operation, changes):
    return {
        "status": "ok",
        "result": "planned",
        "operation": operation,
        "changes": changes,
    }

def build_plan(desired_interface, current_interface):
    """
    Compare desired interface state with normalized current state
    and return a plan according to specs/plan-spec-v1.md.
    """
    if current_interface is None:
        changes = {
            field: {"to": desired_interface[field]}
            for field in MANAGED_FIELDS
        }
        return _plan_result("create", changes)

    changes = {}
    for field in MANAGED_FIELDS:
        desired_value = desired_interface[field]
        current_value = current_interface.get(field)
        if current_value != desired_value:
            changes[field] = {"from": current_value, "to": desired_value}

    if not changes:
        return _plan_result("no_change", {})
    return _plan_result("update", changes)
