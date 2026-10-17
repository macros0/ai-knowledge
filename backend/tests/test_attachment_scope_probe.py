import hashlib
from pathlib import Path
import subprocess
import sys


def test_read_only_cli_preserves_original_and_reports_no_attachments(tmp_path):
    from openpyxl import Workbook

    source = tmp_path / "root.xlsx"
    book = Workbook()
    book.active.append(["root evidence"])
    book.save(source)
    before = hashlib.sha256(source.read_bytes()).digest()
    output = tmp_path / "report.json"
    script = Path(__file__).parents[1] / "scripts/inspect_attachment_scope.py"
    result = subprocess.run(
        [sys.executable, str(script), "--file", str(source), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stderr
    import json

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["inventory"]["nodes"] == []
    assert report["recommendation"]["requires_review"] is False
    assert hashlib.sha256(source.read_bytes()).digest() == before
    assert set(p.name for p in tmp_path.iterdir()) == {"root.xlsx", "report.json", "test.db"}
