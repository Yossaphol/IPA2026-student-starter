import yaml

ALLOWED_ROUTERS = {
    "10.0.29.101",
    "10.0.29.102",
    "10.0.29.103",
    "10.0.29.104",
    "10.0.29.105",
}

METHOD_ACTIONS = {
    "restconf": {"status", "plan", "apply"},
    "netconf": {"status", "plan", "apply"},
    "ansible": {"apply"},
    "netmiko-textfsm": {"status", "delete"},
}

FULL_STATE_ACTIONS = {"plan", "apply"}

ADMIN_STATES = {"up", "down"}

def error(code):
    """Return an error response with the given result code."""
    return {"status": "error", "result": code}

def _is_missing(value):
    """Treat an absent, null, or blank value as missing."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def load_request_file(path):
    """
    Read and parse a YAML request file.

    Return (data, None) when the root is a mapping,
    or (None, invalid_yaml response) otherwise.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, ValueError, OSError):
        return None, error("invalid_yaml")

    if not isinstance(data, dict):
        return None, error("invalid_yaml")

    return data, None

def validate_request(data):
    """
    Validate one IPA2026 desired-state request.

    Return a response dictionary according to
    specs/response-spec-v1.md.
    """
    if not isinstance(data, dict):
        return error("invalid_yaml")

    # 1. version must be the YAML integer 1 (not "1", 1.0 or true)
    version = data.get("version")
    if type(version) is not int or version != 1:
        return error("invalid_version")

    # 2. router
    router = data.get("router")
    if _is_missing(router):
        return error("missing_router")
    if not isinstance(router, str) or router not in ALLOWED_ROUTERS:
        return error("invalid_router")

    # 3. method
    method = data.get("method")
    if _is_missing(method):
        return error("missing_method")
    if not isinstance(method, str) or method not in METHOD_ACTIONS:
        return error("invalid_method")

    # 4. action must be supported by the selected method
    action = data.get("action")
    if _is_missing(action):
        return error("missing_action")
    if not isinstance(action, str) or action not in METHOD_ACTIONS[method]:
        return error("invalid_action")

    # 5. desired.interface.name
    desired = data.get("desired")
    if not isinstance(desired, dict):
        return error("missing_desired")

    interface = desired.get("interface")
    if not isinstance(interface, dict):
        return error("missing_interface")

    if _is_missing(interface.get("name")):
        return error("missing_interface_name")

    # 6. complete desired state is required for plan/apply only
    if action in FULL_STATE_ACTIONS:
        if _is_missing(interface.get("ipv4")):
            return error("missing_ipv4")
        if _is_missing(interface.get("description")):
            return error("missing_description")

        admin_state = interface.get("admin_state")
        if _is_missing(admin_state):
            return error("missing_admin_state")
        if not isinstance(admin_state, str) or admin_state not in ADMIN_STATES:
            return error("invalid_admin_state")

    return {"status": "ok", "result": "accepted"}


def process_request_file(path):
    """
    Read a YAML request file, validate it,
    and return a response dictionary.
    """
    data, load_error = load_request_file(path)
    if load_error is not None:
        return load_error

    return validate_request(data)
