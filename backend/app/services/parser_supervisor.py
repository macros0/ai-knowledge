"""Изолированный и ограниченный по ресурсам запуск document parser.

Парсер читает недоверенные ZIP/OLE/MIME/PDF. Он выполняется в дочернем
``spawn``-процессе. POSIX использует RLIMIT_AS и отдельную группу процессов;
Windows — Job Object с hard memory limit и KILL_ON_JOB_CLOSE. Отказ установки
защиты прекращает разбор до открытия входного файла.
"""
from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass
import json
import os
import shutil
import threading
import time
from pathlib import Path

from docparser import PARSER_VERSION, ParseContext, parse_document
from docparser.blocks import Block
from docparser.source_model import SourceNode
from app.services.parser_limits import WindowsParserJob
from app.services.storage import StorageFullError, is_storage_full

_MAX_WORKER_RESPONSE_BYTES = 16 * 1024 * 1024
_WORKER_ENV_ALLOWLIST = frozenset({
    "COMSPEC", "LANG", "LC_ALL", "PATH", "PYTHONIOENCODING", "SYSTEMROOT", "TEMP", "TMP", "TZ", "WINDIR",
})


class ParserWorkerError(RuntimeError):
    """Parser worker завершился без пригодного результата."""


class ParserIsolationError(ParserWorkerError):
    """OS protection could not be established; never run an unguarded parser."""


class ParserTimeoutError(TimeoutError):
    """Parser worker превысил установленное wall-clock время."""


class ParserMemoryLimitError(MemoryError):
    """Parser worker превысил установленный RSS лимит."""


class ParserBusyError(RuntimeError):
    """Все разрешённые parser worker slots заняты."""


@dataclass(frozen=True)
class SupervisedParseResult:
    """Worker output, iterable as the legacy ``(blocks, sources)`` pair."""

    blocks: list
    sources: list
    warnings: list[dict]
    parser_version: str

    def __iter__(self):
        yield self.blocks
        yield self.sources


_slot_lock = threading.Lock()
_slots_by_limit: dict[int, threading.BoundedSemaphore] = {}


def _acquire_parser_slot(max_concurrent: int) -> threading.BoundedSemaphore:
    """Берёт неблокирующий slot, общий для preview и pipeline данного процесса."""
    with _slot_lock:
        semaphore = _slots_by_limit.setdefault(max_concurrent, threading.BoundedSemaphore(max_concurrent))
    if not semaphore.acquire(blocking=False):
        raise ParserBusyError(f"Все {max_concurrent} parser worker slots заняты")
    return semaphore


def _set_posix_memory_limit(max_memory_bytes: int) -> None:
    if os.name == "nt":
        return
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, max_memory_bytes))
    except (ImportError, OSError, ValueError) as exc:
        raise ParserIsolationError("parser isolation unavailable (RLIMIT_AS)") from exc


def _minimal_worker_environment() -> None:
    """Keep OS necessities but never backend credentials in the parser child."""
    allowed = {key: value for key, value in os.environ.items() if key.upper() in _WORKER_ENV_ALLOWLIST}
    os.environ.clear()
    os.environ.update(allowed)


def _send_worker_message(send, message: dict) -> None:
    """Send bounded JSON, never a pickle-backed Connection.send payload."""
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > _MAX_WORKER_RESPONSE_BYTES:
        payload = b'{"ok":false,"error":"parser worker response exceeds limit"}'
    send.send_bytes(payload)


def _safe_worker_error(exc: BaseException) -> str:
    """Never send parser/library messages that may contain document content."""
    return f"parser worker failed ({type(exc).__name__})"


def _worker_error_code(exc: BaseException) -> str:
    if is_storage_full(exc):
        return "storage_full"
    if isinstance(exc, ParserIsolationError):
        return "isolation_unavailable"
    return "memory_limit" if isinstance(exc, MemoryError) else "parse_failed"


def _worker(
    send, path: str, filename: str, attachments_dir: str, max_memory_bytes: int,
    mail_enabled: bool = True,
) -> None:
    try:
        context = ParseContext(filename, mail_enabled=mail_enabled)
        blocks = parse_document(path, filename, attachments_dir=attachments_dir, context=context)
        _send_worker_message(
            send,
            {
                "ok": True,
                "blocks": [block.__dict__ for block in blocks],
                "sources": [source.__dict__ for source in context.sources],
                "warnings": context.warnings,
                "parser_version": context.parser_version,
            }
        )
    except BaseException as exc:  # child boundary: return diagnostics, never crash parent
        _send_worker_message(send, {"ok": False, "error": _safe_worker_error(exc),
                                   "code": _worker_error_code(exc)})
    finally:
        send.close()


def _worker_entry(
    target, send, path: str, filename: str, attachments_dir: str,
    max_memory_bytes: int, mail_enabled: bool, admission, group_ready,
) -> None:
    """Apply the process boundary before any parser or injected worker runs."""
    try:
        admission.wait()  # Windows parent assigns the job before releasing this.
        _minimal_worker_environment()
        if os.name != "nt":
            try:
                os.setsid()
                group_ready.set()
            except OSError as exc:
                raise ParserIsolationError("parser isolation unavailable (process group)") from exc
            _set_posix_memory_limit(max_memory_bytes)
        if target is _worker:
            target(send, path, filename, attachments_dir, max_memory_bytes, mail_enabled)
        else:
            target(send, path, filename, attachments_dir, max_memory_bytes)
    except BaseException as exc:
        _send_worker_message(send, {"ok": False, "error": _safe_worker_error(exc), "code": _worker_error_code(exc)})
    finally:
        send.close()


def _decode_worker_message(payload: bytes) -> SupervisedParseResult:
    if not payload or len(payload) > _MAX_WORKER_RESPONSE_BYTES:
        raise ParserWorkerError("parser worker вернул недопустимый размер ответа")
    try:
        message = json.loads(payload)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParserWorkerError("parser worker вернул некорректный JSON") from exc
    if not isinstance(message, dict) or not isinstance(message.get("ok"), bool):
        raise ParserWorkerError("parser worker вернул некорректную схему ответа")
    if not message["ok"]:
        error = message.get("error")
        if not isinstance(error, str) or len(error) > 1_000:
            raise ParserWorkerError("parser worker вернул некорректную ошибку")
        if message.get("code") == "storage_full":
            raise StorageFullError()
        if message.get("code") == "memory_limit":
            raise ParserMemoryLimitError("Разбор файла превысил лимит памяти")
        if message.get("code") == "isolation_unavailable":
            raise ParserIsolationError("parser isolation unavailable")
        raise ParserWorkerError(error)
    blocks = message.get("blocks")
    sources = message.get("sources")
    warnings = message.get("warnings", [])
    parser_version = message.get("parser_version", PARSER_VERSION)
    if not (
        isinstance(blocks, list)
        and isinstance(sources, list)
        and isinstance(warnings, list)
        and isinstance(parser_version, str)
        and len(parser_version) <= 64
    ):
        raise ParserWorkerError("parser worker вернул некорректную схему ответа")
    try:
        if not all(isinstance(block, dict) for block in blocks):
            raise TypeError("blocks must be objects")
        if not all(isinstance(source, dict) for source in sources):
            raise TypeError("sources must be objects")
        if not all(isinstance(warning, dict) for warning in warnings):
            raise TypeError("warnings must be objects")
        return SupervisedParseResult(
            blocks=[Block(**block) for block in blocks],
            sources=[SourceNode(**source) for source in sources],
            warnings=warnings,
            parser_version=parser_version,
        )
    except (TypeError, ValueError) as exc:
        raise ParserWorkerError("parser worker вернул некорректную схему ответа") from exc


def _windows_rss_bytes(pid: int) -> int | None:
    """WorkingSetSize via WinAPI; returns None if the OS rejects inspection."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    process = kernel.OpenProcess(0x0410, False, pid)
    if not process:
        return None
    try:
        counters = Counters(cb=ctypes.sizeof(Counters))
        ok = psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
        return int(counters.WorkingSetSize) if ok else None
    finally:
        kernel.CloseHandle(process)


def _terminate(process) -> None:
    if process.is_alive():
        process.terminate()
    process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)
    process.close()


def parse_document_supervised(
    path: str | Path,
    filename: str,
    *,
    attachments_dir: str | Path,
    timeout_seconds: float,
    max_memory_mb: int,
    max_concurrent: int = 2,
    mail_enabled: bool = True,
    _worker_target=None,
) -> SupervisedParseResult:
    """Parse in a killable child and return blocks, sources and diagnostics.

    ``attachments_dir`` belongs exclusively to this attempt. On any worker
    failure it is removed, so retry/resume cannot consume partial artifacts.
    """
    if timeout_seconds <= 0 or max_memory_mb < 64 or max_concurrent < 1:
        raise ValueError("Invalid parser supervisor limits")
    slot = _acquire_parser_slot(max_concurrent)
    dest = Path(attachments_dir)
    dest_existed = dest.exists()
    receive = send = process = job = group_ready = None
    started = succeeded = False
    try:
        dest.mkdir(parents=True, exist_ok=True)
        context = mp.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        admission = context.Event()
        group_ready = context.Event()
        limit = max_memory_mb * 1024 * 1024
        if os.name == "nt":
            try:
                job = WindowsParserJob(limit)
            except OSError as exc:
                raise ParserIsolationError("parser isolation unavailable (Job Object)") from exc
        process = context.Process(
            target=_worker_entry,
            args=(_worker_target or _worker, send, str(path), filename, str(dest), limit, mail_enabled,
                  admission, group_ready),
            daemon=True,
        )
        deadline = time.monotonic() + timeout_seconds
        process.start()
        started = True
        send.close()
        if job is not None:
            try:
                job.assign(process.pid)
            except OSError as exc:
                raise ParserIsolationError("parser isolation unavailable (job assignment)") from exc
        admission.set()
        while True:
            if job is not None and job.memory_exceeded():
                raise ParserMemoryLimitError(f"Разбор файла превысил лимит {max_memory_mb} MiB")
            if receive.poll(0.05):
                try:
                    payload = receive.recv_bytes(maxlength=_MAX_WORKER_RESPONSE_BYTES)
                except (EOFError, OSError) as exc:
                    if job is not None and job.memory_exceeded():
                        raise ParserMemoryLimitError("Разбор файла превысил лимит памяти") from exc
                    raise ParserWorkerError("parser worker завершился без результата") from exc
                result = _decode_worker_message(payload)
                succeeded = True
                return result
            if time.monotonic() >= deadline:
                raise ParserTimeoutError(f"Разбор файла превысил лимит {timeout_seconds:g} с")
            rss = _windows_rss_bytes(process.pid)
            if rss is not None and rss > limit:
                raise ParserMemoryLimitError(f"Разбор файла превысил лимит {max_memory_mb} MiB")
            if not process.is_alive():
                if job is not None and job.memory_exceeded():
                    raise ParserMemoryLimitError("Разбор файла превысил лимит памяти")
                # The worker may send its final response and exit after the
                # preceding empty poll. Drain it through normal validation.
                if receive.poll():
                    continue
                raise ParserWorkerError("parser worker завершился без результата")
    finally:
        try:
            # Close the complete Windows job on success too: a helper process
            # must not survive merely because its parent returned valid JSON.
            if job is not None:
                job.close()
            if started and os.name != "nt" and group_ready.is_set():
                import signal

                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            try:
                if started:
                    _terminate(process)
                elif process is not None:
                    process.close()
            finally:
                if receive is not None:
                    receive.close()
                if send is not None:
                    send.close()
                if not succeeded and not dest_existed:
                    shutil.rmtree(dest, ignore_errors=True)
                slot.release()
