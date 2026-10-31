"""Local parser scratch is never the durable artifact location."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.diagnostics.paths import assert_no_links, canonical_root
from .contract import BlobIntegrityError, BlobRef, BlobStore


@contextmanager
def materialize(ref: BlobRef, *, store: BlobStore, scratch_root: Path):
    root = canonical_root(scratch_root)
    assert_no_links(root, root)
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="artifact-", dir=root) as directory:
        path = Path(directory) / "source"
        digest, size = hashlib.sha256(), 0
        with store.open(ref) as source, path.open("xb") as target:
            while chunk := source.read(64 * 1024):
                size += len(chunk)
                if size > ref.size:
                    raise BlobIntegrityError("Artifact size mismatch")
                digest.update(chunk)
                target.write(chunk)
        if size != ref.size or digest.hexdigest() != ref.sha256:
            raise BlobIntegrityError("Artifact integrity check failed")
        yield path
