"""Real hostile inputs must stay bounded and release the actual spawn worker.

Removing MIME admission, ZIP preflight, RTF refusal or supervisor cleanup
must break these observable output/resource/recovery checks.
"""
import base64
import hashlib
from email.message import EmailMessage
import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import struct
import threading
import time
import zipfile

import pytest

from app.services.parser_supervisor import (
    ParserMemoryLimitError,
    ParserWorkerError,
    _windows_rss_bytes,
    parse_document_supervised,
)


def _resources(pid):
    if os.name != "nt":
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        rss = int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
        cpu = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
        return rss, cpu
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return None
        rss = _windows_rss_bytes(pid)
        cpu = sum((value.dwHighDateTime << 32) | value.dwLowDateTime for value in times[2:]) / 10_000_000
        return (rss, cpu) if rss is not None else None
    finally:
        kernel.CloseHandle(handle)


def _zip_bomb():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"x" * (4 * 1024 * 1024))
    # Genuine expansion ratio exceeds the production 1000:1 boundary.
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        info = archive.infolist()[0]
        assert info.file_size / info.compress_size > 1000
    return buffer.getvalue()


def _fixture(case, folder):
    if case.startswith("base64"):
        body = base64.b64encode(b"x" * (51 * 1024 * 1024))
        if case == "base64-malformed":
            body = b"!" + body
        raw = (b"From: synthetic@example.test\r\nMIME-Version: 1.0\r\n"
               b"Content-Type: multipart/mixed; boundary=hostile\r\n\r\n"
               b"--hostile\r\nContent-Type: text/plain\r\n\r\nReadable parent\r\n"
               b"--hostile\r\nContent-Type: application/octet-stream\r\n"
               b'Content-Disposition: attachment; filename="large.bin"\r\n'
               b"Content-Transfer-Encoding: base64\r\n\r\n" + body + b"\r\n--hostile--\r\n")
        suffix = ".eml"
    elif case == "html-text":
        raw = (b"From: synthetic@example.test\nContent-Type: text/html; charset=utf-8\n\n<p>"
               + b"Visible text " * 240_000 + b"</p>")
        suffix = ".eml"
    elif case == "rtf-expansion":
        fixture_path = Path(__file__).resolve().parents[2] / "doc-parser/tests/mail_fixtures.py"
        spec = importlib.util.spec_from_file_location("adversarial_mail_fixtures", fixture_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Deliberately hostile compressed-RTF header declares 1GiB output.
        # Production must refuse this unsupported stream without expanding it.
        raw = module.compound_bytes({
            "__properties_version1.0": bytes(32),
            "__substg1.0_0037001F": "Hostile RTF\0".encode("utf-16-le"),
            "__substg1.0_10090102": struct.pack("<IIII", 16, 1024**3, 0x75465A4C, 0) + b"\x00" * 4,
        })
        suffix = ".msg"
    elif case == "zip-root":
        raw, suffix = _zip_bomb(), ".docx"
    elif case == "zip-child":
        mail = EmailMessage()
        mail["From"] = "synthetic@example.test"
        mail.set_content("Readable parent")
        mail.add_attachment(_zip_bomb(), maintype="application", subtype="octet-stream", filename="bomb.docx")
        mail.add_attachment(b"bounded proof", maintype="application", subtype="octet-stream", filename="proof.bin")
        raw, suffix = bytes(mail), ".eml"
    else:
        mail = EmailMessage()
        mail["From"] = "synthetic@example.test"
        mail.set_content("Readable parent")
        for index in range(1500):
            mail.add_attachment(b"x", maintype="application", subtype="octet-stream", filename=f"{index}.bin")
        raw, suffix = bytes(mail), ".eml"
    path = folder / (case + suffix)
    path.write_bytes(raw)
    return path


@pytest.mark.parametrize("case", ["base64-valid", "base64-malformed", "html-text", "rtf-expansion", "zip-root", "zip-child", "mime-nodes"])
def test_real_hostile_mail_is_bounded_and_next_parse_recovers(tmp_path, monkeypatch, case):
    from tests.test_upload_development import login, make_client

    client = make_client(tmp_path, monkeypatch)
    login(client)
    source = _fixture(case, tmp_path)
    dest = tmp_path / "attachments"
    before = {child.pid for child in multiprocessing.active_children()}
    samples = []
    heartbeat = []
    heartbeat_errors = []
    stop = threading.Event()

    def sample():
        next_request = 0
        while not stop.is_set():
            for child in multiprocessing.active_children():
                if child.pid in before:
                    continue
                try:
                    resource = _resources(child.pid)
                    if resource:
                        samples.append(resource)
                        if time.monotonic() >= next_request:
                            try:
                                heartbeat.append(client.get("/api/documents").status_code)
                            except Exception as exc:
                                heartbeat_errors.append(type(exc).__name__)
                            next_request = time.monotonic() + 0.2
                except (OSError, ValueError, IndexError):
                    pass
            stop.wait(0.02)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    started = time.monotonic()
    result, failure, failure_detail = None, None, None
    try:
        try:
            result = parse_document_supervised(source, source.name, attachments_dir=dest,
                                              timeout_seconds=20, max_memory_mb=1024)
        except (ParserWorkerError, ParserMemoryLimitError) as exc:
            failure = type(exc).__name__
            failure_detail = str(exc)
    finally:
        elapsed = time.monotonic() - started
        stop.set()
        sampler.join(timeout=2)
    assert elapsed < 25
    assert not ({child.pid for child in multiprocessing.active_children()} - before)
    assert samples, "Resource sampler must observe the actual worker"
    assert heartbeat and all(status == 200 for status in heartbeat)
    assert not heartbeat_errors
    assert max(rss for rss, _ in samples) <= 1024 * 1024 * 1024
    assert max(cpu for _, cpu in samples) < 20
    disk_bytes = sum(path.stat().st_size for path in dest.rglob("*") if path.is_file()) if dest.exists() else 0
    assert disk_bytes < 2 * 1024 * 1024
    if failure:
        assert not dest.exists(), "Failed attempt must not leave partial artifacts"
        assert case == "zip-root", "These bounded non-root fixtures must retain usable content"
        assert "ArchiveLimitError" in failure_detail
    else:
        codes = {warning["code"] for warning in result.warnings}
        if case.startswith("base64"):
            assert "attachment_size_exceeded" in codes
            assert any("Readable parent" in block.text for block in result.blocks)
            assert disk_bytes == 0
        elif case == "html-text":
            assert sum(len(block.text) for block in result.blocks) <= 2_000_000
            assert "text_limit_exceeded" in codes
        elif case == "rtf-expansion":
            assert "unsupported_rtf_body" in codes
            assert disk_bytes == 0
        elif case == "zip-child":
            assert "attachment_parse_failed" in codes
            assert any(path.read_bytes() == b"bounded proof" for path in dest.rglob("*") if path.is_file())
            assert any("Readable parent" in block.text for block in result.blocks)
        elif case == "mime-nodes":
            assert "mime_count_exceeded" in codes
            assert len(result.sources) <= 1002
        else:
            pytest.fail("Unsafe root archive was accepted")
    control = tmp_path / "control.eml"
    control.write_bytes(b"From: synthetic@example.test\n\nRecovery proof")
    recovered = parse_document_supervised(control, control.name, attachments_dir=tmp_path / "control-output",
                                         timeout_seconds=20, max_memory_mb=1024)
    assert any(block.text == "Recovery proof" for block in recovered.blocks)
    assert client.get("/api/documents").status_code == 200
    assert not ({child.pid for child in multiprocessing.active_children()} - before)
    evidence = os.environ.get("MAIL_ADVERSARIAL_REPORT_DIR")
    if evidence:
        output = Path(evidence)
        output.mkdir(parents=True, exist_ok=True)
        (output / f"{case}.json").write_text(json.dumps({
            "case": case, "input_bytes": source.stat().st_size, "elapsed_seconds": elapsed,
            "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "worker_rss_max_bytes": max(rss for rss, _ in samples),
            "worker_cpu_max_seconds": max(cpu for _, cpu in samples),
            "retained_disk_bytes": disk_bytes, "controlled_failure": failure,
            "warnings": result.warnings if result else [], "recovery": True,
            "http_requests_during_parse": len(heartbeat), "http_errors": heartbeat_errors,
        }, indent=2), encoding="utf-8")
