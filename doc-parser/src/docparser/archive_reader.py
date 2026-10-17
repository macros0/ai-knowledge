"""Structured archive access; archive names never become output paths."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from docparser.attachment_policy import TraversalLimits


class ArchiveReadError(ValueError):
    def __init__(self, message, *, status="archive_unreadable"):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ArchiveEntry:
    entry_key: str
    display_path: str
    size: int
    compressed_size: int | None = None
    is_dir: bool = False
    is_special: bool = False
    encrypted: bool = False


@dataclass
class ExtractionReport:
    extracted_bytes: int = 0


def safe_member_name(name):
    normalized = unicodedata.normalize("NFC", name.replace("\\", "/"))
    parts = normalized.rstrip("/").split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or PureWindowsPath(normalized).drive
        or any(p in {"", ".", ".."} or ":" in p or "\x00" in p for p in parts)
    ):
        raise ArchiveReadError("Unsafe archive member path", status="archive_unsafe")
    return normalized.rstrip("/")


class ArchiveReader:
    def __init__(self, path, limits):
        self.path = Path(path)
        self.limits = limits
        self._entries = None
        self._total = 0

    def entries(self):
        if self._entries is None:
            entries = self._read_entries()
            if len(entries) > self.limits.max_archive_entries:
                raise ArchiveReadError("Archive entry limit exceeded", status="skipped_count")
            seen = set()
            total = 0
            for entry in entries:
                name = safe_member_name(entry.display_path).casefold()
                if name in seen or entry.is_special:
                    raise ArchiveReadError(
                        "Ambiguous or special archive member", status="archive_unsafe"
                    )
                seen.add(name)
                if entry.size < 0 or entry.size > self.limits.max_payload_bytes:
                    raise ArchiveReadError("Archive payload limit exceeded", status="skipped_size")
                total += entry.size
                if (
                    entry.compressed_size is not None
                    and entry.size / max(1, entry.compressed_size)
                    > self.limits.max_compression_ratio
                ):
                    raise ArchiveReadError(
                        "Archive compression limit exceeded", status="skipped_size"
                    )
            if (
                total > self.limits.max_total_bytes
                or total / max(1, self.path.stat().st_size) > self.limits.max_compression_ratio
            ):
                raise ArchiveReadError("Archive expansion limit exceeded", status="skipped_size")
            self._entries = entries
        return iter(self._entries)

    def extract_selected(self, entry_keys, sink_factory):
        entries = list(self.entries())
        known = {e.entry_key for e in entries}
        if not entry_keys.issubset(known):
            raise ArchiveReadError("Unknown archive entry")
        selected = [
            entry for entry in entries if entry.entry_key in entry_keys and not entry.is_dir
        ]
        if any(e.encrypted for e in selected):
            raise ArchiveReadError("Archive password required", status="password_required")
        report = ExtractionReport()
        self._extract(selected, sink_factory, report)
        return report

    def charge(self, entry, count, report):
        self._total += count
        report.extracted_bytes += count
        if self._total > self.limits.max_total_bytes:
            raise ArchiveReadError("Actual archive output limit exceeded", status="skipped_size")

    def copy(self, entry, stream, sink, report):
        count = 0
        while chunk := stream.read(65536):
            count += len(chunk)
            if count > min(entry.size, self.limits.max_payload_bytes):
                raise ArchiveReadError(
                    "Actual archive payload limit exceeded", status="skipped_size"
                )
            self.charge(entry, len(chunk), report)
            sink.write(chunk)
        if count != entry.size:
            raise ArchiveReadError("Archive member size mismatch")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        pass


def open_archive(
    path: Path, *, format: str, limits: TraversalLimits, helper_path: Path | None
) -> ArchiveReader:
    try:
        if format == "zip":
            from docparser.archive_zip import ZipArchiveReader

            return ZipArchiveReader(path, limits)
        if format == "7z":
            from docparser.archive_7z import SevenZipArchiveReader

            return SevenZipArchiveReader(path, limits)
        if format == "rar":
            from docparser.archive_rar import RarArchiveReader

            return RarArchiveReader(path, limits, helper_path)
    except (ImportError, OSError) as exc:
        raise ArchiveReadError(
            "Archive backend unavailable", status="archive_backend_unavailable"
        ) from exc
    raise ArchiveReadError("Unsupported archive format")
