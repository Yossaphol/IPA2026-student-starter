from app.planner import build_plan

def plan_interface(
    router,
    desired_interface,
    get_interface_state,
):
    """Build a Part 2 plan from backend status."""
    state = get_interface_state(router, desired_interface["name"])

    if state["status"] != "ok":
        return state

    if state["result"] == "found":
        current_interface = state["interface"]
    else:
        current_interface = None

    return build_plan(desired_interface, current_interface)
