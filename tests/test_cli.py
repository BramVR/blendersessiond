from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from blendersessiond.state import STATE_DIR_ENV_VAR


@pytest.mark.parametrize("verb", ["doctor", "start", "status", "stop"])
def test_relative_state_override_is_machine_readable_error(verb: str) -> None:
    environment = dict(os.environ)
    environment[STATE_DIR_ENV_VAR] = "relative/state"

    completed = subprocess.run(
        [sys.executable, "-m", "blendersessiond", verb, "--json"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["status"] == "error"
    assert payload["command"] == verb
    assert STATE_DIR_ENV_VAR in payload["message"]
