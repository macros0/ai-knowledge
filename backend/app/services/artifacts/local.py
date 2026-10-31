"""Immutable filesystem blobs, atomically published without replacing existing IDs."""
from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path
import stat
import sys
from uuid import uuid4

from app.services.diagnostics.paths import assert_no_links, canonical_root
from app.services.storage import StorageFullError, is_storage_full
from .contract import BlobConflict, BlobIntegrityError, BlobLeaseLost, BlobNotFound, BlobRef, BlobUnavailable, validate_object_id

BLOCK = 64 * 1024


def _storage_failure(exc):
    return StorageFullError() if is_storage_full(exc) else BlobUnavailable("Artifact storage unavailable")


class _RangeReader(io.RawIOBase):
    def __init__(self, source, remaining, guard=None):
        self.source, self.remaining = source, remaining
        self.guard = guard

    def readable(self):
        return True

    def read(self, size=-1):
        if self.closed:
            raise ValueError("Artifact reader closed")
        if self.guard:
            self.guard()
        size = self.remaining if size is None or size < 0 else min(size, self.remaining)
        try:
            value = self.source.read(size)
        except OSError as exc:
            raise _storage_failure(exc) from None
        if self.guard:
            self.guard()
        self.remaining -= len(value)
        return value

    def readinto(self, buffer):
        value = self.read(len(buffer))
        buffer[:len(value)] = value
        return len(value)


class LocalBlobStore:
    def __init__(self, root: Path, *, store_id: str):
        self.root = canonical_root(root)
        self.store_id = store_id
        self._safe(self.root)
        for folder in ("objects", "tmp"):
            path = self._safe(self.root / folder)
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise _storage_failure(exc) from None

    def _safe(self, path):
        if not path.is_relative_to(self.root):
            raise ValueError("Artifact path escapes root")
        try:
            assert_no_links(path, self.root)
        except OSError as exc:
            raise _storage_failure(exc) from None
        return path

    def _path(self, ref):
        if not isinstance(ref, BlobRef) or ref.store_id != self.store_id:
            raise ValueError("Foreign artifact store")
        return self._safe(self.root / ref.key)

    @staticmethod
    def _digest(handle, guard=None):
        digest, size = hashlib.sha256(), 0
        while True:
            if guard:
                guard()
            chunk = handle.read(BLOCK)
            if guard:
                guard()
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        return digest.hexdigest(), size

    def put(self, source, *, object_id: str, media_type: str) -> BlobRef:
        validate_object_id(object_id)
        # Validate metadata before reading or creating anything.
        BlobRef(object_id, f"objects/{object_id}", "0" * 64, 0, media_type, self.store_id)
        temporary = self._safe(self.root / "tmp" / uuid4().hex)
        try:
            digest, size = hashlib.sha256(), 0
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(fd, "wb") as handle:
                for chunk in iter(lambda: source.read(BLOCK), b""):
                    if not isinstance(chunk, bytes):
                        raise ValueError("Artifact source must provide bytes")
                    handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            ref = BlobRef(object_id, f"objects/{object_id}", digest.hexdigest(), size, media_type, self.store_id)
            target = self._path(ref)
            try:
                os.link(temporary, target)
            except FileExistsError:
                try:
                    self.stat(ref)
                except BlobIntegrityError:
                    raise BlobConflict("Artifact identity already has different bytes") from None
            if os.name != "nt":
                directory = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            return ref
        except OSError as exc:
            raise _storage_failure(exc) from None
        finally:
            active_error = sys.exc_info()[0] is not None
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                if not active_error:
                    raise _storage_failure(exc) from None

    @contextmanager
    def open(self, ref: BlobRef, *, start: int = 0, end: int | None = None, guard=None):
        end = ref.size if end is None else end
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= ref.size:
            raise ValueError("Invalid artifact range")
        path = self._path(ref)
        if guard:
            guard()
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        except FileNotFoundError:
            raise BlobNotFound("Artifact not found") from None
        except OSError:
            raise BlobUnavailable("Artifact storage unavailable") from None
        handle, reader = None, None
        try:
            try:
                handle = os.fdopen(fd, "rb")
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise BlobIntegrityError("Artifact is not a regular file")
                actual = self._digest(handle, guard) if guard else self._digest(handle)
                if actual != (ref.sha256, ref.size):
                    raise BlobIntegrityError("Artifact integrity check failed")
                handle.seek(start)
            except BlobLeaseLost:
                raise
            except OSError as exc:
                raise _storage_failure(exc) from None
            reader = _RangeReader(handle, end - start, guard)
            yield reader
        finally:
            active_error = sys.exc_info()[0] is not None
            try:
                if reader is not None:
                    reader.close()
                if handle is not None:
                    handle.close()
                else:
                    os.close(fd)
            except OSError as exc:
                if not active_error:
                    raise _storage_failure(exc) from None

    def stat(self, ref: BlobRef) -> BlobRef:
        with self.open(ref):
            return ref

    def delete(self, ref: BlobRef) -> None:
        path = self._path(ref)
        try:
            if not path.exists():
                return
            self.stat(ref)
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise _storage_failure(exc) from None
