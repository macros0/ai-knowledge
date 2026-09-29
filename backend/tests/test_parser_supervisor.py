from email.message import EmailMessage
import os
import sys
import json
from pathlib import Path
import time

import pytest

from app.services.parser_supervisor import (
    ParserBusyError,
    ParserMemoryLimitError,
    ParserTimeoutError,
    ParserWorkerError,
    _set_posix_memory_limit,
    _send_worker_message,
    _safe_worker_error,
    _acquire_parser_slot,
    parse_document_supervised,
    _decode_worker_message,
)


def test_worker_storage_full_code_becomes_safe_domain_error():
    from app.services.storage import StorageFullError

    payload = json.dumps({"ok": False, "code": "storage_full", "error": "PRIVATE_SOURCE_MARKER"}).encode()
    with pytest.raises(StorageFullError) as caught:
        _decode_worker_message(payload)
    assert caught.value.code == "storage_full"
    assert "PRIVATE_SOURCE_MARKER" not in str(caught.value)


def test_worker_error_text_alone_does_not_claim_full_disk():
    payload = json.dumps({"ok": False, "code": "parse_failed", "error": "No space left on device"}).encode()
    with pytest.raises(ParserWorkerError):
        _decode_worker_message(payload)


def _sleeping_worker(send, path, filename, attachments_dir, max_memory_bytes):
    time.sleep(10)


def _crashing_worker(send, path, filename, attachments_dir, max_memory_bytes):
    os._exit(17)


def _late_result_worker(send, path, filename, attachments_dir, max_memory_bytes):
    release = Path(attachments_dir) / "release"
    deadline = time.monotonic() + 15
    while not release.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Test did not release worker")
        time.sleep(0.01)
    if filename == "malformed.eml":
        send.send_bytes(b"invalid JSON")
    elif filename == "error.eml":
        _send_worker_message(send, {"ok": False, "error": "synthetic parser failure"})
    else:
        _send_worker_message(send, {"ok": True, "blocks": [], "sources": [],
                                    "warnings": [{"code": "late_result"}]})


@pytest.mark.parametrize("filename", ["success.eml", "malformed.eml", "error.eml"])
def test_supervisor_reads_result_sent_between_empty_poll_and_worker_exit(tmp_path, monkeypatch, filename):
    from app.services import parser_supervisor as supervisor

    attachments = tmp_path / "attachments"
    exit_codes = []

    def finish_worker_after_empty_poll(pid):
        worker = next(child for child in supervisor.mp.active_children() if child.pid == pid)
        (attachments / "release").touch()
        worker.join(timeout=10)
        assert not worker.is_alive(), "Worker must finish before supervisor checks liveness"
        exit_codes.append(worker.exitcode)
        return None

    monkeypatch.setattr(supervisor, "_process_rss_bytes", finish_worker_after_empty_poll)

    def parse():
        return parse_document_supervised(
            tmp_path / filename, filename, attachments_dir=attachments,
            timeout_seconds=20, max_memory_mb=1024, _worker_target=_late_result_worker,
        )

    if filename == "success.eml":
        assert parse().warnings == [{"code": "late_result"}]
        assert attachments.exists()
    else:
        expected = "некорректный JSON" if filename == "malformed.eml" else "synthetic parser failure"
        with pytest.raises(ParserWorkerError, match=expected):
            parse()
        assert not attachments.exists()
    assert exit_codes == [0]


def _memory_hog_worker(send, path, filename, attachments_dir, max_memory_bytes):
    _buffer = bytearray(160 * 1024 * 1024)
    time.sleep(10)


def _posix_memory_hog_worker(send, path, filename, attachments_dir, max_memory_bytes):
    _set_posix_memory_limit(max_memory_bytes)
    try:
        _buffer = bytearray(160 * 1024 * 1024)
    except MemoryError:
        _send_worker_message(send, {"ok": False, "error": "MemoryError: RLIMIT_AS enforced"})
        return
    _send_worker_message(send, {"ok": False, "error": "RLIMIT_AS did not reject allocation"})


def test_supervised_parser_returns_blocks_and_sources_from_spawn_worker(tmp_path: Path):
    message = EmailMessage()
    message["Subject"] = "Проверка supervisor"
    message.set_content("Тело письма")
    source = tmp_path / "mail.eml"
    source.write_bytes(bytes(message))

    blocks, sources = parse_document_supervised(
        source, source.name, attachments_dir=tmp_path / "attachments", timeout_seconds=20, max_memory_mb=1024
    )

    assert [block.text for block in blocks] == ["Проверка supervisor", "Тело письма"]
    assert sources[0].source_id == "root"
    assert sources[0].metadata["mail"] is True


def test_supervised_parser_disables_embedded_mail_without_losing_original(tmp_path: Path):
    from pypdf import PdfWriter

    payload = b"From: sender@example.test\nSubject: Decision\n\nPRIVATE MAIL BODY"
    source = tmp_path / "container.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_attachment("decision.eml", payload)
    with source.open("wb") as output:
        writer.write(output)

    result = parse_document_supervised(
        source, source.name, attachments_dir=tmp_path / "attachments",
        timeout_seconds=20, max_memory_mb=1024, mail_enabled=False,
    )

    assert result.parser_version.endswith("-mail-disabled")
    assert result.warnings == [{"code": "mail_import_disabled", "source_id": "root/0"}]
    assert not any("PRIVATE MAIL BODY" in block.text for block in result.blocks)
    marker = next(block for block in result.blocks if block.meta.get("source_id") == "root/0")
    assert marker.meta["extraction_status"] == "skipped_disabled"
    assert Path(marker.meta["saved_path"]).read_bytes() == payload


def test_supervised_parser_cleans_partial_storage_after_worker_error(tmp_path: Path):
    source = tmp_path / "unsupported.txt"
    source.write_text("not supported", encoding="utf-8")
    attachments = tmp_path / "attachments"

    with pytest.raises(ParserWorkerError):
        parse_document_supervised(
            source, source.name, attachments_dir=attachments, timeout_seconds=20, max_memory_mb=1024
        )

    assert not attachments.exists()


def test_supervised_parser_terminates_a_hung_worker(tmp_path: Path):
    attachments = tmp_path / "attachments"
    with pytest.raises(ParserTimeoutError):
        parse_document_supervised(
            tmp_path / "unused.eml",
            "unused.eml",
            attachments_dir=attachments,
            timeout_seconds=1,
            max_memory_mb=1024,
            _worker_target=_sleeping_worker,
        )
    assert not attachments.exists()


def test_supervised_parser_normalizes_crashed_worker_and_cleans_storage(tmp_path: Path):
    attachments = tmp_path / "attachments"
    with pytest.raises(ParserWorkerError, match="завершился без результата"):
        parse_document_supervised(
            tmp_path / "unused.eml",
            "unused.eml",
            attachments_dir=attachments,
            timeout_seconds=20,
            max_memory_mb=1024,
            _worker_target=_crashing_worker,
        )
    assert not attachments.exists()


def test_parser_slots_reject_a_third_concurrent_worker_without_waiting():
    first = _acquire_parser_slot(2)
    second = _acquire_parser_slot(2)
    try:
        with pytest.raises(ParserBusyError):
            _acquire_parser_slot(2)
    finally:
        second.release()
        first.release()


def _held_worker(send, path, filename, attachments_dir, max_memory_bytes):
    from docparser import PARSER_VERSION

    pending = Path(attachments_dir, "started.pid.tmp")
    pending.write_text(str(os.getpid()), encoding="ascii")
    pending.replace(Path(attachments_dir, "started.pid"))
    while not Path(path).exists():
        time.sleep(0.02)
    _send_worker_message(send, {
        "ok": True, "blocks": [], "sources": [], "warnings": [],
        "parser_version": PARSER_VERSION,
    })


def test_concurrent_supervised_calls_bound_live_workers_and_recover_slots(tmp_path):
    """Preview and background callers share admission while OS workers are alive."""
    from concurrent.futures import ThreadPoolExecutor
    import multiprocessing

    release = tmp_path / "release"
    destinations = [tmp_path / "preview", tmp_path / "background"]

    def parse(destination):
        return parse_document_supervised(
            release, "held.eml", attachments_dir=destination,
            timeout_seconds=20, max_memory_mb=1024, max_concurrent=2,
            _worker_target=_held_worker,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(parse, destination) for destination in destinations]
        try:
            deadline = time.monotonic() + 10
            while not all((destination / "started.pid").is_file() for destination in destinations):
                assert time.monotonic() < deadline, "two parser workers did not start"
                time.sleep(0.02)
            pids = {int((destination / "started.pid").read_text()) for destination in destinations}
            live_pids = {child.pid for child in multiprocessing.active_children() if child.is_alive()}
            assert len(pids) == 2 and pids <= live_pids
            with pytest.raises(ParserBusyError):
                parse(tmp_path / "third")
            assert not (tmp_path / "third").exists()
        finally:
            release.touch()
        assert all(future.result(timeout=15).blocks == [] for future in futures)

    assert not pids.intersection(child.pid for child in multiprocessing.active_children())
    assert parse(tmp_path / "after-completion").blocks == []
    slots = []
    try:
        for _ in range(2):
            slots.append(_acquire_parser_slot(2))
    finally:
        for slot in slots:
            slot.release()


@pytest.mark.skipif(os.name != "nt", reason="Windows RSS monitor is tested on Windows")
def test_supervised_parser_terminates_worker_over_windows_rss_limit(tmp_path: Path):
    attachments = tmp_path / "attachments"
    with pytest.raises(ParserMemoryLimitError):
        parse_document_supervised(
            tmp_path / "unused.eml",
            "unused.eml",
            attachments_dir=attachments,
            timeout_seconds=20,
            max_memory_mb=64,
            _worker_target=_memory_hog_worker,
        )
    assert not attachments.exists()


@pytest.mark.skipif(
    os.name == "nt" or sys.platform == "darwin",
    reason="RLIMIT_AS is a Linux acceptance test; macOS does not support it",
)
def test_supervised_parser_enforces_posix_address_space_limit(tmp_path: Path):
    attachments = tmp_path / "attachments"
    with pytest.raises(ParserWorkerError, match="MemoryError: RLIMIT_AS enforced"):
        parse_document_supervised(
            tmp_path / "unused.eml",
            "unused.eml",
            attachments_dir=attachments,
            timeout_seconds=20,
            max_memory_mb=64,
            _worker_target=_posix_memory_hog_worker,
        )
    assert not attachments.exists()


def _warning_worker(send, path, filename, attachments_dir, max_memory_bytes):
    _send_worker_message(
        send,
        {
            "ok": True,
            "blocks": [],
            "sources": [],
            "warnings": [{"code": "mail_parse_failed", "source_id": "root/0"}],
            "parser_version": "mail-sources-v1",
        }
    )


def test_supervised_parser_preserves_worker_parse_diagnostics(tmp_path: Path):
    result = parse_document_supervised(
        tmp_path / "unused.eml",
        "unused.eml",
        attachments_dir=tmp_path / "attachments",
        timeout_seconds=20,
        max_memory_mb=1024,
        _worker_target=_warning_worker,
    )

    blocks, sources = result
    assert blocks == []
    assert sources == []
    assert result.warnings == [{"code": "mail_parse_failed", "source_id": "root/0"}]
    assert result.parser_version == "mail-sources-v1"


def _oversized_response_worker(send, path, filename, attachments_dir, max_memory_bytes):
    send.send_bytes(b"x" * (17 * 1024 * 1024))


def _environment_worker(send, path, filename, attachments_dir, max_memory_bytes):
    _send_worker_message(
        send,
        {
            "ok": True,
            "blocks": [],
            "sources": [],
            "warnings": [{"database_url": os.getenv("DATABASE_URL"), "llm_key": os.getenv("OPENROUTER_API_KEY")}],
            "parser_version": "mail-sources-v1",
        },
    )


def test_supervisor_rejects_oversized_non_json_worker_response_and_cleans_storage(tmp_path: Path):
    attachments = tmp_path / "attachments"
    with pytest.raises(ParserWorkerError):
        parse_document_supervised(
            tmp_path / "unused.eml",
            "unused.eml",
            attachments_dir=attachments,
            timeout_seconds=20,
            max_memory_mb=1024,
            _worker_target=_oversized_response_worker,
        )
    assert not attachments.exists()


def test_supervisor_scrubs_backend_credentials_from_worker_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")

    result = parse_document_supervised(
        tmp_path / "unused.eml",
        "unused.eml",
        attachments_dir=tmp_path / "attachments",
        timeout_seconds=20,
        max_memory_mb=1024,
        _worker_target=_environment_worker,
    )

    assert result.warnings == [{"database_url": None, "llm_key": None}]


def test_worker_error_message_never_echoes_untrusted_content():
    assert _safe_worker_error(ValueError("private-mail-body@example.test")) == "parser worker failed (ValueError)"


@pytest.mark.skipif(os.name != "nt", reason="Windows hard memory gate")
def test_windows_job_rejects_allocation_before_parent_polling():
    import subprocess
    import sys
    from app.services.parser_limits import WindowsParserJob

    # Minimal child isolates memory allocation from optional OpenBLAS startup.
    # No parent RSS polling or termination is used to make allocation fail.
    script = "import sys\nsys.stdin.readline()\ntry:\n b=bytearray(400*1024*1024)\nexcept MemoryError:\n print('denied')\nelse:\n print('allowed')"
    child = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    job = WindowsParserJob(256 * 1024 * 1024)
    try:
        job.assign(child.pid)
        stdout, stderr = child.communicate("admitted\n", timeout=10)
        assert child.returncode == 0, stderr
        assert stdout.strip() == "denied"
        assert job.memory_exceeded()
    finally:
        job.close()
        child.kill() if child.poll() is None else None
        child.wait(timeout=5)


def _descendant_worker(send, path, filename, attachments_dir, max_memory_bytes):
    import subprocess
    import sys

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    Path(path).write_text(str(child.pid), encoding="ascii")
    Path(f"{path}.worker").write_text(str(os.getpid()), encoding="ascii")
    if filename == "timeout":
        time.sleep(30)
    elif filename == "crash":
        os._exit(17)
    else:
        _send_worker_message(send, {"ok": True, "blocks": [], "sources": []})


@pytest.mark.skipif(os.name != "nt", reason="Windows job descendant cleanup")
@pytest.mark.parametrize("outcome", ["success", "timeout", "crash"])
def test_windows_job_removes_descendants_on_every_exit(tmp_path, outcome):
    import ctypes
    from ctypes import wintypes

    pid_file = tmp_path / "child.pid"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    child_handle = None
    try:
        arguments = dict(attachments_dir=tmp_path / "attachments", timeout_seconds=3,
                         max_memory_mb=1024, _worker_target=_descendant_worker)
        if outcome == "timeout":
            with pytest.raises(ParserTimeoutError):
                parse_document_supervised(pid_file, outcome, **arguments)
        elif outcome == "crash":
            with pytest.raises(ParserWorkerError):
                parse_document_supervised(pid_file, outcome, **arguments)
        else:
            parse_document_supervised(pid_file, outcome, **arguments)
        assert pid_file.exists(), "worker must actually have spawned its child"
        child_handle = kernel.OpenProcess(0x00100001, False, int(pid_file.read_text()))
        if child_handle:
            assert kernel.WaitForSingleObject(child_handle, 2000) == 0, "orphan process survived parser exit"
        else:
            assert ctypes.get_last_error() == 87, "access failure is not proof of process exit"
    finally:
        if child_handle:
            kernel.TerminateProcess(child_handle, 1)
            kernel.WaitForSingleObject(child_handle, 2000)
            kernel.CloseHandle(child_handle)


@pytest.mark.skipif(
    os.name == "nt" or sys.platform == "darwin",
    reason="POSIX limit setup failure; macOS enforces memory by RSS polling",
)
def test_posix_limit_installation_failure_is_not_silently_ignored(monkeypatch):
    import resource

    def refuse(*args):
        raise OSError("injected OS rejection")

    monkeypatch.setattr(resource, "setrlimit", refuse)
    with pytest.raises(ParserWorkerError, match="isolation"):
        _set_posix_memory_limit(256 * 1024 * 1024)


def _started_marker_worker(send, path, filename, attachments_dir, max_memory_bytes):
    Path(path).write_text("input opened", encoding="ascii")
    _send_worker_message(send, {"ok": True, "blocks": [], "sources": []})


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS RSS monitor")
def test_supervised_parser_terminates_worker_over_darwin_rss_limit(tmp_path: Path):
    # RLIMIT_AS на macOS нет: без опроса RSS этот воркер занял бы 160 MiB.
    attachments = tmp_path / "attachments"
    with pytest.raises(ParserMemoryLimitError):
        parse_document_supervised(
            tmp_path / "unused.eml",
            "unused.eml",
            attachments_dir=attachments,
            timeout_seconds=20,
            max_memory_mb=64,
            _worker_target=_memory_hog_worker,
        )
    assert not attachments.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS RSS monitor setup failure")
def test_darwin_without_rss_monitor_never_runs_worker_and_releases_slot(tmp_path, monkeypatch):
    from app.services import parser_supervisor

    monkeypatch.setattr(parser_supervisor, "_darwin_rss_bytes", lambda pid: None)
    for attempt in range(2):
        dest = tmp_path / f"attempt-{attempt}"
        marker = tmp_path / f"opened-{attempt}"
        with pytest.raises(parser_supervisor.ParserIsolationError, match="RSS monitor"):
            parse_document_supervised(marker, "unused.eml", attachments_dir=dest,
                                      timeout_seconds=10, max_memory_mb=256, max_concurrent=1,
                                      _worker_target=_started_marker_worker)
        assert not marker.exists()
        assert not dest.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows protection setup failures")
@pytest.mark.parametrize("stage", ["create", "assign"])
def test_windows_job_setup_failure_never_runs_worker_and_releases_slot(tmp_path, monkeypatch, stage):
    from app.services import parser_supervisor

    real_job = parser_supervisor.WindowsParserJob

    def refuse(*args):
        raise OSError("injected OS rejection")

    def create(limit):
        if stage == "create":
            return refuse()
        job = real_job(limit)
        job.assign = refuse
        return job

    monkeypatch.setattr(parser_supervisor, "WindowsParserJob", create)
    for attempt in range(2):
        dest = tmp_path / f"attempt-{attempt}"
        marker = tmp_path / f"opened-{attempt}"
        with pytest.raises(parser_supervisor.ParserIsolationError, match="isolation"):
            parse_document_supervised(marker, "unused.eml", attachments_dir=dest,
                                      timeout_seconds=10, max_memory_mb=256, max_concurrent=1,
                                      _worker_target=_started_marker_worker)
        assert not marker.exists()
        assert not dest.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group cleanup")
@pytest.mark.parametrize("outcome", ["success", "timeout", "crash"])
def test_posix_group_removes_descendants_on_every_exit(tmp_path, outcome):
    import signal

    pid_file = tmp_path / "child.pid"
    # Full-suite coverage can use the old timeout before the child writes its PID marker.
    arguments = dict(attachments_dir=tmp_path / "attachments", timeout_seconds=15,
                     max_memory_mb=1024, _worker_target=_descendant_worker)
    if outcome == "timeout":
        with pytest.raises(ParserTimeoutError):
            parse_document_supervised(pid_file, outcome, **arguments)
    elif outcome == "crash":
        with pytest.raises(ParserWorkerError):
            parse_document_supervised(pid_file, outcome, **arguments)
    else:
        parse_document_supervised(pid_file, outcome, **arguments)
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    # Docker PID 1 may defer orphan reaping; a zombie is already terminated.
    status = Path(f"/proc/{pid}/stat")
    deadline = time.monotonic() + 2
    while status.exists() and ") Z " not in status.read_text() and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        assert not status.exists() or ") Z " in status.read_text(), "orphan survived group cleanup"
    finally:
        if status.exists() and ") Z " not in status.read_text():
            os.kill(pid, signal.SIGKILL)


@pytest.mark.skipif(os.name != "nt", reason="Windows kill-on-close after supervisor death")
def test_windows_job_kills_worker_and_descendant_when_supervisor_dies(tmp_path):
    import ctypes
    import subprocess
    import sys
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    pid_file = tmp_path / "child.pid"
    script = (
        "import sys; from tests.test_parser_supervisor import _descendant_worker; "
        "from app.services.parser_supervisor import parse_document_supervised; "
        "parse_document_supervised(sys.argv[1], 'timeout', attachments_dir=sys.argv[2], "
        "timeout_seconds=25, max_memory_mb=1024, _worker_target=_descendant_worker)"
    )
    parent = subprocess.Popen([sys.executable, "-c", script, str(pid_file), str(tmp_path / "attachments")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    handles = []
    try:
        deadline = time.monotonic() + 15
        worker_pid_file = Path(f"{pid_file}.worker")
        while not worker_pid_file.exists() and parent.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert worker_pid_file.exists(), "supervisor did not reach actual descendant creation"
        for file in (pid_file, worker_pid_file):
            handle = kernel.OpenProcess(0x00100001, False, int(file.read_text()))
            assert handle, "both processes must be observed alive before parent death"
            handles.append(handle)
            assert kernel.WaitForSingleObject(handle, 0) == 258
        parent.kill()
        parent.wait(timeout=5)
        for handle in handles:
            assert kernel.WaitForSingleObject(handle, 3000) == 0, "child survived supervisor death"
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.communicate(timeout=5)
        for handle in handles:
            kernel.TerminateProcess(handle, 1)
            kernel.WaitForSingleObject(handle, 2000)
            kernel.CloseHandle(handle)
