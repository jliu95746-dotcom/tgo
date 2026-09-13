"""No running broker or business tasks are needed for these probe contract tests."""
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

path = Path(__file__).parents[1] / "worker_probe.py"
spec = importlib.util.spec_from_file_location("worker_probe", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WorkerProbeTests(unittest.TestCase):
    def test_only_requested_worker_pong_is_accepted(self):
        for response, expected in [
            (None, False), ({}, False),
            ({"other": {"ok": "pong"}}, False),
            ({"target": {"error": "offline"}}, False),
            ({"target": {"ok": "pong"}}, True),
        ]:
            app = Mock()
            app.control.inspect.return_value.ping.return_value = response
            with patch.dict(sys.modules, {"celery": SimpleNamespace(Celery=Mock(return_value=app))}):
                with patch.dict("os.environ", {"CELERY_BROKER_URL": "redis://fixture/3"}):
                    self.assertEqual(module.probe_worker("target"), expected)
            app.control.inspect.assert_called_once_with(destination=["target"], timeout=1.0)
            app.close.assert_called_once()

    def test_probe_has_a_process_deadline_and_suppresses_raw_output(self):
        for result, expected in [
            (subprocess.CompletedProcess([], 0, "secret", "secret"), True),
            (subprocess.CompletedProcess([], 1, "secret", "secret"), False),
        ]:
            with patch.object(module.subprocess, "run", return_value=result) as run:
                self.assertEqual(module.bounded_probe("target"), expected)
                self.assertEqual(run.call_args.kwargs["timeout"], 8)
                self.assertTrue(run.call_args.kwargs["capture_output"])
        with patch.object(module.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 8)):
            self.assertFalse(module.bounded_probe("target"))

    def test_missing_broker_never_probes_an_implicit_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(module.probe_worker("target"))


if __name__ == "__main__":
    unittest.main()
