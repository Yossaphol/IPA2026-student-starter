import os
import xml.etree.ElementTree as ET
from ipaddress import IPv4Interface
from xml.sax.saxutils import escape

from ncclient import manager
from ncclient.operations.errors import TimeoutExpiredError
from ncclient.transport.errors import AuthenticationError, TransportError

from app.backends import planning
from app.core import error

NETCONF_PORT = 830
TIMEOUT = 10

NC_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
IF_NS = "urn:ietf:params:xml:ns:yang:ietf-interfaces"
IP_NS = "urn:ietf:params:xml:ns:yang:ietf-ip"
NATIVE_NS = "http://cisco.com/ns/yang/Cisco-IOS-XE-native"

def _connect(router):
    return manager.connect(
        host=router,
        port=NETCONF_PORT,
        username=os.environ.get("ROUTER_USER", ""),
        password=os.environ.get("ROUTER_PASS", ""),
        hostkey_verify=False,
        allow_agent=False,
        look_for_keys=False,
        timeout=TIMEOUT,
    )


def _real_get_config(router, filter_xml):
    with _connect(router) as m:
        reply = m.get_config(source="running", filter=("subtree", filter_xml))
        return reply.data_xml


def _real_edit_config(router, config_xml):
    with _connect(router) as m:
        m.edit_config(target="running", config=config_xml)


def _exception_to_error(exc):
    if isinstance(exc, (PermissionError, AuthenticationError)):
        return error("authentication_failed")
    if isinstance(
        exc,
        (ConnectionError, TimeoutError, TransportError, TimeoutExpiredError),
    ):
        return error("connection_failed")
    return error("backend_failed")


def _loopback_number(interface_name):
    return int(interface_name[len("Loopback"):])


def _text(parent, path):
    element = parent.find(path)
    if element is None or element.text is None:
        return None
    return element.text.strip()


def _filter_xml(interface_name):
    return (
        f'<interfaces xmlns="{IF_NS}">'
        "<interface>"
        f"<name>{escape(interface_name)}</name>"
        "</interface>"
        "</interfaces>"
    )


def _normalize(data_xml, interface_name):
    root = ET.fromstring(data_xml)

    for interface in root.iter(f"{{{IF_NS}}}interface"):
        if _text(interface, f"{{{IF_NS}}}name") != interface_name:
            continue

        address = interface.find(f"{{{IP_NS}}}ipv4/{{{IP_NS}}}address")
        ipv4 = None
        if address is not None:
            ip = _text(address, f"{{{IP_NS}}}ip")
            netmask = _text(address, f"{{{IP_NS}}}netmask")
            if ip and netmask:
                ipv4 = str(IPv4Interface(f"{ip}/{netmask}"))

        enabled = _text(interface, f"{{{IF_NS}}}enabled")

        return {
            "name": interface_name,
            "ipv4": ipv4,
            "description": _text(interface, f"{{{IF_NS}}}description"),
            "admin_state": "down" if enabled == "false" else "up",
        }

    return None


def _build_config(desired_interface):
    ipv4 = IPv4Interface(desired_interface["ipv4"])
    number = _loopback_number(desired_interface["name"])
    shutdown = "<shutdown/>" if desired_interface["admin_state"] == "down" else ""

    return f"""<config xmlns="{NC_NS}" xmlns:nc="{NC_NS}">
  <native xmlns="{NATIVE_NS}">
    <interface>
      <Loopback nc:operation="replace">
        <name>{number}</name>
        <description>{escape(desired_interface["description"])}</description>
        <ip>
          <address>
            <primary>
              <address>{ipv4.ip}</address>
              <mask>{ipv4.netmask}</mask>
            </primary>
          </address>
        </ip>
        {shutdown}
      </Loopback>
    </interface>
  </native>
</config>"""


def get_interface_state(
    router,
    interface_name,
    netconf_get_config=None,
):
    """Return normalized interface status using NETCONF."""
    if netconf_get_config is None:
        netconf_get_config = _real_get_config

    try:
        data_xml = netconf_get_config(router, _filter_xml(interface_name))
        interface = _normalize(data_xml, interface_name)
    except Exception as exc:
        return _exception_to_error(exc)

    if interface is None:
        return {"status": "ok", "result": "not_found", "interface": None}
    return {"status": "ok", "result": "found", "interface": interface}


def plan_interface(
    router,
    desired_interface,
    netconf_get_config=None,
):
    """Return create/update/no_change plan using NETCONF status."""
    return planning.plan_interface(
        router,
        desired_interface,
        lambda r, name: get_interface_state(r, name, netconf_get_config),
    )


def apply_interface(
    router,
    desired_interface,
    netconf_edit_config=None,
):
    """Apply complete desired interface state using NETCONF."""
    if netconf_edit_config is None:
        netconf_edit_config = _real_edit_config

    try:
        netconf_edit_config(router, _build_config(desired_interface))
    except Exception as exc:
        return _exception_to_error(exc)

    return {"status": "ok", "result": "applied"}