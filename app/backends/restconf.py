import os
import ssl
from ipaddress import IPv4Interface

import requests
import urllib3
from requests.adapters import HTTPAdapter

from app.backends import planning
from app.core import error

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "Accept": "application/yang-data+json",
    "Content-Type": "application/yang-data+json",
}

TIMEOUT = 10

APPLY_OK_CODES = {200, 201, 204}

def _auth():
    return (
        os.environ.get("ROUTER_USER", ""),
        os.environ.get("ROUTER_PASS", ""),
    )

class _LegacyTLSAdapter(HTTPAdapter):
    """Allow the router's legacy TLS renegotiation, which OpenSSL 3 blocks."""

    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.options |= ssl.OP_LEGACY_SERVER_CONNECT
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


def _session():
    session = requests.Session()
    session.mount("https://", _LegacyTLSAdapter())
    return session


def _real_get(url):
    with _session() as session:
        return session.get(
            url, auth=_auth(), headers=HEADERS, verify=False, timeout=TIMEOUT
        )


def _real_put(url, json):
    with _session() as session:
        return session.put(
            url, json=json, auth=_auth(), headers=HEADERS, verify=False, timeout=TIMEOUT
        )

def _exception_to_error(exc):
    if isinstance(exc, PermissionError):
        return error("authentication_failed")
    if isinstance(
        exc,
        (
            ConnectionError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ),
    ):
        return error("connection_failed")
    return error("backend_failed")


def _status_code_error(status_code):
    if status_code in (401, 403):
        return error("authentication_failed")
    return error("backend_failed")


def _loopback_number(interface_name):
    return int(interface_name[len("Loopback"):])


def _normalize(data):
    raw = data["ietf-interfaces:interface"]
    if isinstance(raw, list):
        raw = raw[0]

    addresses = raw.get("ietf-ip:ipv4", {}).get("address", [])
    ipv4 = None
    if addresses:
        first = addresses[0]
        ipv4 = str(IPv4Interface(f"{first['ip']}/{first['netmask']}"))

    return {
        "name": raw["name"],
        "ipv4": ipv4,
        "description": raw.get("description"),
        "admin_state": "up" if raw.get("enabled", True) else "down",
    }


def _build_payload(desired_interface):
    ipv4 = IPv4Interface(desired_interface["ipv4"])
    loopback = {
        "name": _loopback_number(desired_interface["name"]),
        "description": desired_interface["description"],
        "ip": {
            "address": {
                "primary": {
                    "address": str(ipv4.ip),
                    "mask": str(ipv4.netmask),
                }
            }
        },
    }
    if desired_interface["admin_state"] == "down":
        loopback["shutdown"] = [None]
    return {"Cisco-IOS-XE-native:Loopback": loopback}

def get_interface_state(
    router,
    interface_name,
    http_get=None,
):
    """Return normalized interface status using RESTCONF."""
    if http_get is None:
        http_get = _real_get

    url = (
        f"https://{router}/restconf/data/"
        f"ietf-interfaces:interfaces/interface={interface_name}"
    )

    try:
        response = http_get(url)
        if response.status_code == 404:
            return {"status": "ok", "result": "not_found", "interface": None}
        if response.status_code != 200:
            return _status_code_error(response.status_code)
        interface = _normalize(response.json())
    except Exception as exc:
        return _exception_to_error(exc)

    return {"status": "ok", "result": "found", "interface": interface}


def plan_interface(
    router,
    desired_interface,
    http_get=None,
):
    """Return create/update/no_change plan using RESTCONF status."""
    return planning.plan_interface(
        router,
        desired_interface,
        lambda r, name: get_interface_state(r, name, http_get),
    )


def apply_interface(
    router,
    desired_interface,
    http_put=None,
):
    """Apply complete desired interface state using RESTCONF."""
    if http_put is None:
        http_put = _real_put

    try:
        number = _loopback_number(desired_interface["name"])
        url = (
            f"https://{router}/restconf/data/"
            f"Cisco-IOS-XE-native:native/interface/Loopback={number}"
        )
        response = http_put(url, json=_build_payload(desired_interface))
    except Exception as exc:
        return _exception_to_error(exc)

    if response.status_code in APPLY_OK_CODES:
        return {"status": "ok", "result": "applied"}
    return _status_code_error(response.status_code)
