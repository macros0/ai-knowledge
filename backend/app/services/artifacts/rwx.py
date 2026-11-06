"""Shared filesystem adapter; mount/CSI guarantees are an external acceptance gate."""
from pathlib import Path

from app.services.diagnostics.paths import assert_no_links, canonical_root
from .local import LocalBlobStore, _storage_failure


class RwxBlobStore(LocalBlobStore):
    def __init__(self, root: Path, *, store_id: str):
        try:
            root = canonical_root(root)
            assert_no_links(root, root)
            if not root.is_dir():
                raise ValueError("RWX requires an existing shared root")
        except OSError as exc:
            raise _storage_failure(exc) from None
        # Never create a missing mount root and silently fall back to local disk.
        super().__init__(root, store_id=store_id)
