import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module", ["sorta.storage", "sorta.executor", "sorta.application.service", "sorta.api.main"]
)
def test_public_modules_import_in_fresh_process(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
