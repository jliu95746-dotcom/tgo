"""Read-only, process-bounded Celery readiness probe for native startup."""
import os
from pathlib import Path
import subprocess
import sys


def probe_worker(node: str) -> bool:
    broker = os.environ.get("CELERY_BROKER_URL")
    if not broker:
        return False
    from celery import Celery

    app = Celery("native_readiness", broker=broker)
    try:
        app.conf.update(
            broker_connection_timeout=2,
            broker_connection_retry=False,
            broker_transport_options={"socket_connect_timeout": 2, "socket_timeout": 2},
        )
        response = app.control.inspect(destination=[node], timeout=1.0).ping()
        return isinstance(response, dict) and response.get(node) == {"ok": "pong"}
    except Exception:
        return False
    finally:
        app.close()


def bounded_probe(node: str) -> bool:
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--child", node],
            timeout=8,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


if __name__ == "__main__":
    child = len(sys.argv) == 3 and sys.argv[1] == "--child"
    if len(sys.argv) != (3 if child else 2):
        raise SystemExit(2)
    ready = probe_worker(sys.argv[2]) if child else bounded_probe(sys.argv[1])
    if not child:
        print("ready" if ready else "unavailable")
    raise SystemExit(0 if ready else 1)
