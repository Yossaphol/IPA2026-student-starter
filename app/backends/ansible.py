import json
import os
import subprocess
import sys
from ipaddress import IPv4Interface
from pathlib import Path

from app.core import error

ANSIBLE_DIR = Path(__file__).resolve().parents[2] / "ansible"
PLAYBOOK = ANSIBLE_DIR / "apply_interface.yml"
INVENTORY = ANSIBLE_DIR / "inventory.ini"
TIMEOUT = 120

AUTH_MARKERS = ("authentication", "permission denied")
CONNECTION_MARKERS = (
    "unable to connect",
    "unreachable!",
    "timed out",
    "connection refused",
    "no route to host",
)

def _build_variables(router, desired_interface):
    ipv4 = IPv4Interface(desired_interface["ipv4"])
    return {
        "router": router,
        "interface_name": desired_interface["name"],
        "description": desired_interface["description"],
        "ipv4_address": str(ipv4.ip),
        "netmask": str(ipv4.netmask),
        "shutdown": desired_interface["admin_state"] == "down",
    }


def _real_run_playbook(variables):
    command = [
        sys.executable,
        "-m",
        "ansible.cli.playbook",
        "-i",
        str(INVENTORY),
        str(PLAYBOOK),
        "-e",
        json.dumps(variables),
    ]
    env = {
        **os.environ,
        "ANSIBLE_HOST_KEY_CHECKING": "False",
        "ANSIBLE_NOCOLOR": "1",
    }

    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, env=env, timeout=TIMEOUT
        )
    except subprocess.TimeoutExpired as exc:
        raise ConnectionError("ansible-playbook timed out") from exc

    if completed.returncode == 0:
        return

    output = (completed.stdout + completed.stderr).lower()
    if any(marker in output for marker in AUTH_MARKERS):
        raise PermissionError("router rejected the credentials")
    if any(marker in output for marker in CONNECTION_MARKERS):
        raise ConnectionError("router is unreachable")
    raise RuntimeError("ansible-playbook failed")


def _exception_to_error(exc):
    if isinstance(exc, PermissionError):
        return error("authentication_failed")
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return error("connection_failed")
    return error("backend_failed")


def apply_interface(
    router,
    desired_interface,
    run_playbook=None,
):
    """Apply complete desired interface state using Ansible."""
    if run_playbook is None:
        run_playbook = _real_run_playbook

    try:
        run_playbook(_build_variables(router, desired_interface))
    except Exception as exc:
        return _exception_to_error(exc)

    return {"status": "ok", "result": "applied"}