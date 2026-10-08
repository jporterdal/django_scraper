"""Process-level outbound connection guard for demo mode (design D7, defense in depth).

The replay fetcher and demo metadata provider already have no network code;
this guard catches anything else that tries to connect out. Only Unix
sockets and loopback addresses are allowed. Binding/listening is untouched,
so the WSGI server is unaffected.
"""

import ipaddress
import socket

_original_connect = None
_original_connect_ex = None


class DemoEgressBlocked(ConnectionRefusedError):
    """An outbound connection was attempted while demo mode is on."""


def _is_allowed(sock, address):
    if sock.family == getattr(socket, "AF_UNIX", object()):
        return True
    host = address[0] if isinstance(address, tuple) and address else address
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (TypeError, ValueError):
        return False


def _guarded_connect(self, address):
    if not _is_allowed(self, address):
        raise DemoEgressBlocked(f"Outbound connection to {address!r} blocked in demo mode")
    return _original_connect(self, address)


def _guarded_connect_ex(self, address):
    if not _is_allowed(self, address):
        raise DemoEgressBlocked(f"Outbound connection to {address!r} blocked in demo mode")
    return _original_connect_ex(self, address)


def install_egress_guard():
    """Patch ``socket.socket.connect``/``connect_ex``. Idempotent."""
    global _original_connect, _original_connect_ex
    if _original_connect is not None:
        return
    _original_connect = socket.socket.connect
    _original_connect_ex = socket.socket.connect_ex
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex


def uninstall_egress_guard():
    """Restore the original socket methods (used by tests)."""
    global _original_connect, _original_connect_ex
    if _original_connect is None:
        return
    socket.socket.connect = _original_connect
    socket.socket.connect_ex = _original_connect_ex
    _original_connect = None
    _original_connect_ex = None


def egress_guard_installed():
    return _original_connect is not None
