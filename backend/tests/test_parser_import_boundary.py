import subprocess
import sys
from pathlib import Path


def test_parser_supervisor_import_does_not_load_model_runtime():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import app.services.parser_supervisor; assert 'litellm' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
