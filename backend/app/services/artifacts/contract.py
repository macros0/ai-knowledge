"""Immutable artifact references shared by filesystem and future object adapters."""
from dataclasses import dataclass
import re
from typing import BinaryIO, ContextManager, Protocol


class BlobConflict(ValueError):
    pass


class BlobIntegrityError(ValueError):
    pass


class BlobUnavailable(OSError):
    pass


def validate_object_id(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ValueError("Invalid artifact identity")


@dataclass(frozen=True)
class BlobRef:
    object_id: str
    key: str
    sha256: str
    size: int
    media_type: str
    store_id: str

    def __post_init__(self):
        validate_object_id(self.object_id)
        if self.key != f"objects/{self.object_id}":
            raise ValueError("Invalid artifact key")
        if not isinstance(self.sha256, str) or re.fullmatch(r"[a-f0-9]{64}", self.sha256) is None:
            raise ValueError("Invalid artifact digest")
        if type(self.size) is not int or self.size < 0:
            raise ValueError("Invalid artifact size")
        if not isinstance(self.store_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.store_id):
            raise ValueError("Invalid artifact store")
        if (not isinstance(self.media_type, str) or not 1 <= len(self.media_type) <= 255
                or any(ord(char) < 32 or ord(char) > 126 for char in self.media_type)):
            raise ValueError("Invalid artifact media type")


class BlobStore(Protocol):
    def put(self, source: BinaryIO, *, object_id: str, media_type: str) -> BlobRef: ...
    def stat(self, ref: BlobRef) -> BlobRef: ...
    def open(self, ref: BlobRef, *, start: int = 0, end: int | None = None) -> ContextManager[BinaryIO]: ...
    def delete(self, ref: BlobRef) -> None: ...
