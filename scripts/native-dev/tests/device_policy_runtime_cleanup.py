"""Check listener exit without treating Windows TIME_WAIT as a live server."""

import errno
import socket


def require_listener_stopped(port: int) -> None:
    with socket.socket() as probe:
        probe.settimeout(5)
        result = probe.connect_ex(("127.0.0.1", port))
    if result == 0:
        raise RuntimeError(f"Owned test port {port} is still listening")
    if result not in {errno.ECONNREFUSED, 10061}:
        raise RuntimeError(f"Listener exit on port {port} not confirmed: {result}")
