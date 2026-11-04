import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class RuntimeResourceTests(unittest.TestCase):
    variables = (
        "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "OPENCV_FOR_THREADS_NUM",
    )

    def run_bridge_import(self, overrides, *, native=False):
        environment = os.environ.copy()
        for name in self.variables:
            environment.pop(name, None)
        environment.update(overrides)
        script = (
            "import json, os; import tools.wechat_local_bridge; "
            f"settings = {{name: os.environ.get(name) for name in {self.variables!r}}}; "
        )
        if native:
            script += (
                "from tools.wechat_local_bridge import visual_locator; "
                "settings['cv_threads'] = visual_locator.cv2.getNumThreads(); "
            )
        result = subprocess.run(
            [sys.executable, "-c", script + "print(json.dumps(settings))"],
            cwd=Path(__file__).resolve().parents[3],
            env=environment, capture_output=True, text=True, check=True, timeout=15,
        )
        return json.loads(result.stdout)

    def test_fresh_bridge_process_limits_native_library_threads_before_media_imports(self):
        self.assertEqual(
            self.run_bridge_import({}), {name: "1" for name in self.variables},
        )

    def test_explicit_process_resource_settings_are_preserved(self):
        overrides = dict(zip(self.variables, ("2", "3", "4")))
        self.assertEqual(self.run_bridge_import(overrides), overrides)

    def test_visual_runtime_uses_one_thread_by_default(self):
        self.assertEqual(self.run_bridge_import({}, native=True)["cv_threads"], 1)

    def test_visual_runtime_honors_explicit_thread_override(self):
        self.assertEqual(self.run_bridge_import(
            {"OPENCV_FOR_THREADS_NUM": "2"}, native=True,
        )["cv_threads"], 2)
