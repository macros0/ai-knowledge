"""Bounded, conditional S3 storage. No endpoint is contacted at import time."""
from contextlib import contextmanager, suppress
import hashlib
from pathlib import Path
import re
import sys
from tempfile import TemporaryFile
from urllib.parse import urlsplit

from botocore.exceptions import BotoCoreError, ClientError

from app.services.diagnostics.paths import assert_no_links, canonical_root
from app.services.storage import StorageFullError
from .contract import BlobConflict, BlobIntegrityError, BlobLeaseLost, BlobNotFound, BlobRef, BlobUnavailable, validate_object_id
from .local import BLOCK, _RangeReader, _storage_failure

PART = 8 * 1024 * 1024
DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024


def validate_endpoint(endpoint, bucket, prefix, addressing_style="path", allow_http=False):
    try:
        parsed = urlsplit(endpoint)
        valid = (parsed.scheme in ({"https", "http"} if allow_http else {"https"})
                 and parsed.hostname and not parsed.username and not parsed.password
                 and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("Invalid artifact S3 endpoint")
    if (not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket)
            or ".." in bucket or addressing_style not in {"path", "virtual"}):
        raise ValueError("Invalid artifact S3 bucket/addressing style")
    if not isinstance(prefix, str) or (prefix and not re.fullmatch(r"(?:[A-Za-z0-9_-]+/)+", prefix)):
        raise ValueError("Invalid artifact S3 prefix")
    host = parsed.hostname if addressing_style == "path" else f"{bucket}.{parsed.hostname}"
    return parsed.scheme, host, port


class S3BlobStore:
    def inventory_page(self, **kwargs):
        from .inventory import s3_page
        return s3_page(self, **kwargs)

    def inspect_candidate(self, candidate, **kwargs):
        from .inventory import s3_inspect
        return s3_inspect(self, candidate, **kwargs)

    def gc_scope(self, token, **kwargs):
        from .inventory import gc_scope
        return gc_scope(self, token, **kwargs)

    def __init__(self, *, client, endpoint: str, bucket: str, prefix: str,
                 store_id: str, scratch_root: Path, addressing_style="path",
                 allow_http=False, max_bytes=DEFAULT_MAX_BYTES):
        self.authority = validate_endpoint(endpoint, bucket, prefix, addressing_style, allow_http)
        BlobRef("0" * 32, "objects/" + "0" * 32, "0" * 64, 0, "application/octet-stream", store_id)
        if type(max_bytes) is not int or not 0 < max_bytes <= PART * 10000:
            raise ValueError("Invalid artifact size limit")
        self.client, self.bucket, self.prefix, self.store_id = client, bucket, prefix, store_id
        self.max_bytes = max_bytes
        try:
            self.scratch_root = canonical_root(scratch_root)
            assert_no_links(self.scratch_root, self.scratch_root)
            self.scratch_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise self._failure(exc) from None
        self.client.meta.events.register("before-send.s3", self.check_endpoint)

    def check_endpoint(self, request, **kwargs):
        try:
            parsed = urlsplit(request.url)
            actual = (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
            valid = actual == self.authority and not parsed.username and not parsed.password
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise BlobUnavailable("Artifact endpoint redirect refused")

    def _key(self, ref):
        if not isinstance(ref, BlobRef) or ref.store_id != self.store_id:
            raise ValueError("Foreign artifact store")
        return self.prefix + ref.key

    @staticmethod
    def _failure(exc):
        if isinstance(exc, ClientError):
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = exc.response.get("Error", {}).get("Code")
            if code == "NoSuchKey" or (code == "NotFound" and exc.operation_name in {"GetObject", "HeadObject"}):
                return BlobNotFound("Artifact not found")
            if status == 507 or code in {"QuotaExceeded", "XMinioStorageFull", "InsufficientStorage"}:
                return StorageFullError()
        if isinstance(exc, OSError):
            return _storage_failure(exc)
        return BlobUnavailable("Artifact S3 unavailable")

    def _call(self, operation, **kwargs):
        try:
            result = getattr(self.client, operation)(Bucket=self.bucket, **kwargs)
            if result.get("Error"):
                raise BlobUnavailable("Artifact S3 rejected operation")
            return result
        except (ClientError, BotoCoreError, OSError) as exc:
            raise self._failure(exc) from None

    def _conditional(self, operation, **kwargs):
        try:
            result = getattr(self.client, operation)(Bucket=self.bucket, IfNoneMatch="*", **kwargs)
            if result.get("Error"):
                raise BlobUnavailable("Artifact conditional write failed")
            return True
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 412:
                return False
            raise self._failure(exc) from None
        except (BotoCoreError, OSError) as exc:
            raise self._failure(exc) from None

    def put(self, source, *, object_id, media_type):
        validate_object_id(object_id)
        BlobRef(object_id, f"objects/{object_id}", "0" * 64, 0, media_type, self.store_id)
        try:
            with TemporaryFile(dir=self.scratch_root) as spool:
                digest, size = hashlib.sha256(), 0
                for chunk in iter(lambda: source.read(BLOCK), b""):
                    if not isinstance(chunk, bytes):
                        raise ValueError("Artifact source must provide bytes")
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError("Artifact size limit exceeded")
                    spool.write(chunk)
                    digest.update(chunk)
                spool.flush()
                spool.seek(0)
                ref = BlobRef(object_id, f"objects/{object_id}", digest.hexdigest(), size, media_type, self.store_id)
                key = self._key(ref)
                if size <= PART:
                    created = self._conditional("put_object", Key=key, Body=spool, ContentLength=size,
                                                ContentType=media_type, Metadata={"sha256": ref.sha256})
                else:
                    created = self._multipart(spool, key, ref)
                try:
                    self.stat(ref)
                except BlobIntegrityError:
                    if not created:
                        raise BlobConflict("Artifact identity already has different bytes") from None
                    raise
                return ref
        except OSError as exc:
            raise self._failure(exc) from None

    def _multipart(self, spool, key, ref):
        response = self._call("create_multipart_upload", Key=key, ContentType=ref.media_type,
                              Metadata={"sha256": ref.sha256})
        upload_id = response.get("UploadId")
        if not isinstance(upload_id, str) or not upload_id:
            raise BlobUnavailable("Artifact multipart identity unavailable")
        completed = False
        try:
            parts = []
            while chunk := spool.read(PART):
                part = self._call("upload_part", Key=key, UploadId=upload_id,
                                  PartNumber=len(parts) + 1, Body=chunk)
                if not isinstance(part.get("ETag"), str):
                    raise BlobUnavailable("Artifact part receipt unavailable")
                parts.append({"PartNumber": len(parts) + 1, "ETag": part["ETag"]})
            completed = self._conditional("complete_multipart_upload", Key=key, UploadId=upload_id,
                                           MultipartUpload={"Parts": parts})
            return completed
        finally:
            if not completed:
                with suppress(BlobUnavailable, StorageFullError):
                    self._call("abort_multipart_upload", Key=key, UploadId=upload_id)

    def _verified_spool(self, ref, guard=None):
        key = self._key(ref)
        if ref.size > self.max_bytes:
            raise ValueError("Artifact size limit exceeded")
        if guard:
            guard()
        response = self._call("get_object", Key=key)
        body = response.get("Body")
        spool = None
        try:
            if body is None or type(response.get("ContentLength")) is not int or response["ContentLength"] != ref.size:
                raise BlobIntegrityError("Artifact size mismatch")
            spool = TemporaryFile(dir=self.scratch_root)
            digest, size = hashlib.sha256(), 0
            while True:
                if guard:
                    guard()
                chunk = body.read(BLOCK)
                if guard:
                    guard()
                if not chunk:
                    break
                size += len(chunk)
                if size > ref.size:
                    raise BlobIntegrityError("Artifact size mismatch")
                digest.update(chunk)
                spool.write(chunk)
            if size != ref.size or digest.hexdigest() != ref.sha256:
                raise BlobIntegrityError("Artifact integrity check failed")
            spool.seek(0)
            return spool
        except BaseException as exc:
            if spool is not None:
                with suppress(OSError):
                    spool.close()
            if isinstance(exc, BlobLeaseLost):
                raise
            if isinstance(exc, (OSError, BotoCoreError)):
                raise self._failure(exc) from None
            raise
        finally:
            if body is not None:
                with suppress(OSError, BotoCoreError):
                    body.close()

    @contextmanager
    def open(self, ref, *, start=0, end=None, guard=None):
        end = ref.size if end is None else end
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= ref.size:
            raise ValueError("Invalid artifact range")
        spool = self._verified_spool(ref, guard=guard) if guard else self._verified_spool(ref)
        reader = None
        try:
            try:
                spool.seek(start)
            except OSError as exc:
                raise self._failure(exc) from None
            if guard:
                guard()
            reader = _RangeReader(spool, end - start, guard)
            yield reader
        finally:
            active_error = sys.exc_info()[0] is not None
            try:
                if reader is not None:
                    reader.close()
                spool.close()
            except OSError as exc:
                if not active_error:
                    raise self._failure(exc) from None

    def stat(self, ref):
        with self.open(ref):
            return ref

    def delete(self, ref, *, guard=None):
        key = self._key(ref)
        try:
            if guard:
                with self.open(ref, guard=guard):
                    pass
            else:
                self.stat(ref)
        except BlobNotFound:
            return
        if guard:
            guard()
        self._call("delete_object", Key=key)
