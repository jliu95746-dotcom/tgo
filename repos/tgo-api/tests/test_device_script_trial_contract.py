"""Check independent public/private services preserve their wire contract."""

import json
import os
from pathlib import Path
import subprocess

import pytest

from app.schemas.agent_device_script_trials import (
    ScriptTrialReport,
    ScriptTrialStart,
)
from app.schemas.agent_device_script_requests import (
    ScriptTrialControl,
    ScriptTrialRequestState,
)
from app.schemas.agent_device_script_cases import (
    ScriptCaseWrite,
    ScriptCaseValues,
    ScriptCaseRecord,
    ScriptCaseRead,
    ScriptCasePage,
    ScriptResultValues,
    ScriptTrialEvidence,
    ScriptCaseTrialResult,
    ScriptCaseSaveControl,
    ScriptCaseSaveState,
)


@pytest.mark.integration
def test_public_trial_dtos_match_the_actual_ai_wire_schema():
    ai_root = Path(__file__).resolve().parents[2] / "tgo-ai"
    executable = (
        ai_root
        / ".venv"
        / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    if not executable.is_file():
        pytest.skip("The independent AI runtime is required for this contract")
    code = """
import json
from app.schemas.agent_device_script_trials import (
    ScriptTrialStart, ScriptTrialReport,
)
from app.schemas.agent_device_script_requests import (
    ScriptTrialControl, ScriptTrialRequestState,
)
from app.schemas.agent_device_script_cases import (
    ScriptCaseWrite, ScriptCaseValues, ScriptCaseRecord, ScriptCaseRead,
    ScriptCasePage, ScriptResultValues, ScriptTrialEvidence,
    ScriptCaseTrialResult, ScriptCaseSaveControl, ScriptCaseSaveState,
)
print(json.dumps({model.__name__: model.model_json_schema()
                  for model in (ScriptTrialStart, ScriptTrialReport,
                                ScriptTrialControl, ScriptTrialRequestState,
                                ScriptCaseWrite, ScriptCaseValues,
                                ScriptCaseRecord, ScriptCaseRead,
                                ScriptCasePage,
                                ScriptResultValues, ScriptTrialEvidence,
                                ScriptCaseTrialResult, ScriptCaseSaveControl,
                                ScriptCaseSaveState)}))
"""
    result = subprocess.run(
        [str(executable), "-X", "utf8", "-c", code],
        cwd=ai_root,
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    schemas = json.loads(result.stdout)
    for model in (
        ScriptTrialStart,
        ScriptTrialReport,
        ScriptTrialControl,
        ScriptTrialRequestState,
        ScriptCaseWrite,
        ScriptCaseValues,
        ScriptCaseRecord,
        ScriptCaseRead,
        ScriptCasePage,
        ScriptResultValues,
        ScriptTrialEvidence,
        ScriptCaseTrialResult,
        ScriptCaseSaveControl,
        ScriptCaseSaveState,
    ):
        assert model.model_json_schema() == schemas[model.__name__]
