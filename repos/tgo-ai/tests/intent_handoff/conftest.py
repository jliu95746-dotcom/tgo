"""Pure routing tests do not load the application's database fixtures."""

import sys
from pathlib import Path
from types import ModuleType

# Import the real classifier without the unrelated AgentService re-exports in
# app.services.__init__, which require the full agent runtime dependency set.
if "app.services" not in sys.modules:
    services = ModuleType("app.services")
    services.__path__ = [str(Path(__file__).resolve().parents[2] / "app" / "services")]
    sys.modules["app.services"] = services
