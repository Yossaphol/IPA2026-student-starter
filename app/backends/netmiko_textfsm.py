import os

from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)

from app.core import error

TIMEOUT = 10

def _not_found():
    return {"status": "ok", "result": "not_found", "interface": None}


def _connect(router):
    return ConnectHandler(
        device_type="cisco_ios",
        host=router,
        username=os.environ.get("ROUTER_USER", ""),
        password=os.environ.get("ROUTER_PASS", ""),
        conn_timeout=TIMEOUT,
    )


def _as_records(output):
    if isinstance(output, list):
        return output
    if not output.strip() or "% Invalid input" in output:
        return []
    raise RuntimeError("TextFSM could not parse the command output")


def _read(conn, interface_name):
    ip_info = conn.send_command(
        f"show ip interface {interface_name}", use_textfsm=True
    )
    descriptions = conn.send_command(
        "show interfaces description", use_textfsm=True
    )
    return _as_records(ip_info), _as_records(descriptions)


def _full_name(name):
    if name.startswith("Lo") and not name.startswith("Loopback"):
        return "Loopback" + name[len("Lo"):]
    return name


def _find(records, key, interface_name):
    for record in records:
        if _full_name(record.get(key, "")) == interface_name:
            return record
    return None


def _real_get(router, interface_name):
    with _connect(router) as conn:
        return _read(conn, interface_name)


def _real_delete(router, interface_name):
    with _connect(router) as conn:
        ip_info, _ = _read(conn, interface_name)
        if _find(ip_info, "interface", interface_name) is None:
            return "not_found"

        conn.send_config_set([f"no interface {interface_name}"])

        ip_info, _ = _read(conn, interface_name)
        if _find(ip_info, "interface", interface_name) is not None:
            raise RuntimeError(f"{interface_name} still exists after delete")
        return "deleted"


def _exception_to_error(exc):
    if isinstance(exc, (PermissionError, NetmikoAuthenticationException)):
        return error("authentication_failed")
    if isinstance(exc, (ConnectionError, TimeoutError, NetmikoTimeoutException)):
        return error("connection_failed")
    return error("backend_failed")


def _normalize(ip_info, descriptions, interface_name):
    ip_record = _find(ip_info, "interface", interface_name)
    if ip_record is None:
        return None

    addresses = ip_record.get("ip_address") or []
    prefixes = ip_record.get("prefix_length") or []
    ipv4 = None
    if addresses and prefixes:
        ipv4 = f"{addresses[0]}/{prefixes[0]}"

    description_record = _find(descriptions, "port", interface_name)
    description = None
    if description_record is not None:
        description = description_record.get("description") or None

    if ip_record.get("link_status") == "administratively down":
        admin_state = "down"
    else:
        admin_state = "up"

    return {
        "name": interface_name,
        "ipv4": ipv4,
        "description": description,
        "admin_state": admin_state,
    }

def get_interface_state(
    router,
    interface_name,
    netmiko_get=None,
):
    """Return normalized interface status using Netmiko + TextFSM."""
    if netmiko_get is None:
        netmiko_get = _real_get

    try:
        ip_info, descriptions = netmiko_get(router, interface_name)
        interface = _normalize(ip_info, descriptions, interface_name)
    except Exception as exc:
        return _exception_to_error(exc)

    if interface is None:
        return _not_found()
    return {"status": "ok", "result": "found", "interface": interface}


def delete_interface(
    router,
    interface_name,
    netmiko_delete=None,
):
    """Delete an interface using Netmiko."""
    if netmiko_delete is None:
        netmiko_delete = _real_delete

    try:
        outcome = netmiko_delete(router, interface_name)
    except Exception as exc:
        return _exception_to_error(exc)

    if outcome == "deleted":
        return {"status": "ok", "result": "deleted"}
    if outcome == "not_found":
        return _not_found()
    return error("backend_failed")
