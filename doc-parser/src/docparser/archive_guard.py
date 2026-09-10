"""Resource limits for untrusted OOXML ZIP containers.

The compressed upload size is not a useful safety boundary: ZIP members can
expand by orders of magnitude before python-docx/openpyxl see them.
"""
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import zipfile


@dataclass(frozen=True)
class ArchiveLimits:
    max_members: int = 10_000
    max_member_bytes: int = 128 * 1024 * 1024
    max_uncompressed_bytes: int = 512 * 1024 * 1024
    max_compression_ratio: float = 1_000.0


class ArchiveLimitError(ValueError):
    """Raised before an OOXML library can expand an unsafe archive."""


def validate_zip(path: str | Path, limits: ArchiveLimits | None = None) -> None:
    limits = limits or ArchiveLimits()
    try:
        with zipfile.ZipFile(str(path)) as archive:
            members = archive.infolist()
            if len(members) > limits.max_members:
                raise ArchiveLimitError("archive member count exceeds the safety limit")

            total = 0
            seen: set[str] = set()
            for info in members:
                name = info.filename.replace("\\", "/")
                if (
                    name.startswith("/")
                    or PureWindowsPath(name).drive
                    or any(part == ".." for part in name.split("/"))
                ):
                    raise ArchiveLimitError("archive contains an unsafe member path")
                if name in seen:
                    raise ArchiveLimitError("archive contains duplicate member names")
                seen.add(name)
                if info.file_size > limits.max_member_bytes:
                    raise ArchiveLimitError("archive member exceeds the safety limit")
                compressed = max(info.compress_size, 1)
                if info.file_size / compressed > limits.max_compression_ratio:
                    raise ArchiveLimitError("archive compression ratio exceeds the safety limit")
                total += info.file_size
                if total > limits.max_uncompressed_bytes:
                    raise ArchiveLimitError("archive uncompressed size exceeds the safety limit")
    except ArchiveLimitError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveLimitError("invalid ZIP archive") from exc


def validate_member(info: zipfile.ZipInfo, limits: ArchiveLimits | None = None) -> None:
    limits = limits or ArchiveLimits()
    compressed = max(info.compress_size, 1)
    if info.file_size > limits.max_member_bytes:
        raise ArchiveLimitError("archive member exceeds the safety limit")
    if info.file_size / compressed > limits.max_compression_ratio:
        raise ArchiveLimitError("archive compression ratio exceeds the safety limit")
