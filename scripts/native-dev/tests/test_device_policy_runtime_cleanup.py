"""A closed listener may retain TIME_WAIT; a live listener must still fail."""

import socket
from unittest.mock import patch

import pytest

from device_policy_runtime_cleanup import require_listener_stopped


def test_closed_owned_listener_accepts_remaining_connection_history():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen(1)
        with socket.create_connection(("127.0.0.1", port)) as client:
            accepted, _ = listener.accept()
            accepted.close()
            client.recv(1)
    require_listener_stopped(port)


def test_an_actual_listener_is_not_reported_as_stopped():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        with pytest.raises(RuntimeError, match="still listening"):
            require_listener_stopped(listener.getsockname()[1])


def test_timeout_does_not_prove_listener_exit():
    with patch("device_policy_runtime_cleanup.socket.socket") as factory:
        factory.return_value.__enter__.return_value.connect_ex.return_value = 10060
        with pytest.raises(RuntimeError, match="not confirmed"):
            require_listener_stopped(12345)
