"""Provider inventory for GC only; listing tokens are never integrity evidence."""
from dataclasses import dataclass
import hashlib
import heapq
import io
import json
import os
import re
import stat
from uuid import uuid4

from .contract import BlobConflict, BlobIntegrityError, BlobNotFound, BlobRef, validate_object_id
from .local import BLOCK, _storage_failure

MARKER = "gc-owner.json"


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def location_hash(store):
    return _hash([type(store).__name__, os.path.normcase(str(store.root))] if hasattr(store, "root") else
                 ["s3", store.authority, store.bucket, store.prefix])


@dataclass(frozen=True)
class Candidate:
    object_id: str
    store_id: str
    size: int
    fingerprint: str

    def __post_init__(self):
        BlobRef(self.object_id, "objects/" + self.object_id, self.fingerprint, self.size,
                "application/octet-stream", self.store_id)


@dataclass(frozen=True)
class InventoryPage:
    items: tuple[Candidate, ...]
    cursor: str | None


def _limit(limit):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid inventory limit")


def _file_fingerprint(info):
    # Windows path stat and handle fstat disagree about ctime after hard-link
    # publication (creation time versus metadata change time).
    return _hash([info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns])


def local_page(store, *, cursor=None, limit=100):
    _limit(limit)
    if cursor is not None:
        validate_object_id(cursor)
    folder = store._safe(store.root / "objects")
    try:
        # Flat filesystem directories have no portable ordered seek API. Bound
        # memory with a heap; listing costs O(directory size), hashing does not.
        with os.scandir(folder) as entries:
            names = heapq.nsmallest(limit + 1, (entry.name for entry in entries
                if re.fullmatch(r"[0-9a-f]{32}", entry.name) and (cursor is None or entry.name > cursor)))
        items = []
        for name in names[:limit]:
            path = store._safe(folder / name)
            try:
                info = path.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise BlobIntegrityError("Inventory member is not a regular file")
            items.append(Candidate(name, store.store_id, info.st_size, _file_fingerprint(info)))
        return InventoryPage(tuple(items), names[limit - 1] if len(names) > limit else None)
    except OSError as exc:
        raise _storage_failure(exc) from None


def _candidate(store, item, max_bytes):
    if not isinstance(item, Candidate) or item.store_id != store.store_id:
        raise ValueError("Foreign inventory candidate")
    if type(max_bytes) is not int or max_bytes < 1 or item.size > max_bytes:
        raise ValueError("Inventory inspection exceeds byte budget")


def local_inspect(store, item, *, max_bytes, guard=None):
    _candidate(store, item, max_bytes)
    path = store._safe(store.root / "objects" / item.object_id)
    try:
        with path.open("rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or _file_fingerprint(before) != item.fingerprint:
                raise BlobIntegrityError("Inventory member changed")
            digest, size = hashlib.sha256(), 0
            while True:
                if guard:
                    guard()
                block = source.read(BLOCK)
                if not block:
                    break
                size += len(block)
                if size > item.size:
                    raise BlobIntegrityError("Inventory member changed")
                digest.update(block)
            if size != item.size or _file_fingerprint(os.fstat(source.fileno())) != item.fingerprint:
                raise BlobIntegrityError("Inventory member changed")
        return BlobRef(item.object_id, "objects/" + item.object_id, digest.hexdigest(), size, "application/octet-stream", store.store_id)
    except FileNotFoundError:
        raise BlobNotFound("Inventory member disappeared") from None
    except OSError as exc:
        raise _storage_failure(exc) from None


def _s3_fingerprint(value):
    size = value.get("Size", value.get("ContentLength"))
    etag, modified = value.get("ETag"), value.get("LastModified")
    if type(size) is not int or size < 0 or not isinstance(etag, str) or not etag or not hasattr(modified, "isoformat"):
        raise BlobIntegrityError("Incomplete provider inventory")
    return size, _hash([size, etag, modified.isoformat()])


def s3_page(store, *, cursor=None, limit=100):
    _limit(limit)
    if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096 or any(ord(c) < 32 for c in cursor)):
        raise ValueError("Invalid inventory cursor")
    prefix = store.prefix + "objects/"
    result = store._call("list_objects_v2", Prefix=prefix, MaxKeys=limit,
                        **({"ContinuationToken": cursor} if cursor else {}))
    contents = result.get("Contents", [])
    truncated = result.get("IsTruncated")
    next_cursor = result.get("NextContinuationToken") if truncated else None
    if (not isinstance(contents, list) or len(contents) > limit or type(truncated) is not bool
            or truncated and (not isinstance(next_cursor, str) or not 1 <= len(next_cursor) <= 4096 or next_cursor == cursor)):
        raise BlobIntegrityError("Invalid provider inventory page")
    items = []
    for entry in contents:
        key = entry.get("Key")
        if not isinstance(key, str) or not key.startswith(prefix) or not re.fullmatch(r"[0-9a-f]{32}", key[len(prefix):]):
            continue  # Never adopt foreign keys, subdirectories or aliases.
        size, fingerprint = _s3_fingerprint(entry)
        items.append(Candidate(key[len(prefix):], store.store_id, size, fingerprint))
    return InventoryPage(tuple(items), next_cursor)


def s3_inspect(store, item, *, max_bytes, guard=None):
    _candidate(store, item, min(max_bytes, store.max_bytes))
    if guard:
        guard()
    response = store._call("get_object", Key=store.prefix + "objects/" + item.object_id)
    body = response.get("Body")
    try:
        if body is None or _s3_fingerprint(response) != (item.size, item.fingerprint):
            raise BlobIntegrityError("Inventory member changed")
        digest, size = hashlib.sha256(), 0
        while True:
            if guard:
                guard()
            block = body.read(BLOCK)
            if not block:
                break
            size += len(block)
            if size > item.size:
                raise BlobIntegrityError("Inventory member changed")
            digest.update(block)
        if size != item.size:
            raise BlobIntegrityError("Inventory member changed")
        return BlobRef(item.object_id, "objects/" + item.object_id, digest.hexdigest(), size,
                       "application/octet-stream", store.store_id)
    finally:
        if body is not None:
            body.close()


def gc_scope(store, token, *, initialize=False, adopt_existing=False):
    validate_object_id(token)
    expected = json.dumps({"version": 1, "store_id": store.store_id, "token": token}, sort_keys=True).encode()
    local = hasattr(store, "root")
    path = store._safe(store.root / MARKER) if local else store.prefix + MARKER
    def read():
        if local:
            try:
                with path.open("rb") as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise BlobIntegrityError("Invalid GC ownership marker")
                    return source.read(4097)
            except FileNotFoundError:
                return None
        try:
            response = store._call("get_object", Key=path)
        except BlobNotFound:
            return None
        source = response["Body"]
        try:
            return source.read(4097)
        finally:
            source.close()
    actual = read()
    if actual is None and initialize:
        if not adopt_existing:
            page = store.inventory_page(limit=1)
            if page.items or page.cursor is not None:
                raise BlobConflict("Existing store requires explicit GC adoption")
        if local:
            temporary = store._safe(store.root / "tmp" / uuid4().hex)
            try:
                with temporary.open("xb") as output:
                    output.write(expected)
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        else:
            store._conditional("put_object", Key=path, Body=io.BytesIO(expected), ContentLength=len(expected), ContentType="application/json")
        actual = read()
    if actual != expected:
        raise BlobConflict("GC database/storage ownership mismatch")
