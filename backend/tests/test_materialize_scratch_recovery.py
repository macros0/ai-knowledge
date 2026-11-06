"""A later materialization reclaims only abandoned owned parser scratch."""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys

from app.services.artifacts.contract import BlobRef
from app.services.artifacts.materialize import materialize

RAW = b"retained original scratch bytes"
REF = BlobRef("a" * 32, "objects/" + "a" * 32, sha256(RAW).hexdigest(), len(RAW), "application/octet-stream", "scratch-test")


class Store:
    @contextmanager
    def open(self, _ref):
        yield BytesIO(RAW)


def test_materialization_reclaims_owned_scratch_after_real_process_exit(tmp_path):
    script = r'''
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json,os,sys
from pathlib import Path
from app.services.artifacts.contract import BlobRef
from app.services.artifacts.materialize import materialize
raw=b"retained original scratch bytes"
class Store:
 @contextmanager
 def open(self,ref):yield BytesIO(raw)
ref=BlobRef("a"*32,"objects/"+"a"*32,sha256(raw).hexdigest(),len(raw),"application/octet-stream","scratch-test")
root=Path(sys.argv[1])
with materialize(ref,store=Store(),scratch_root=root) as path:
 (root/'exited-path.json').write_text(json.dumps(str(path)))
 os._exit(23)
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, timeout=15)
    assert result.returncode == 23
    abandoned = Path(json.loads((tmp_path / "exited-path.json").read_text()))
    assert abandoned.read_bytes() == RAW
    unrelated = tmp_path / "operator-owned"; unrelated.mkdir(); (unrelated / "keep").write_bytes(b"keep")
    with materialize(REF, store=Store(), scratch_root=tmp_path) as active:
        assert active.read_bytes() == RAW
        assert not abandoned.parent.exists()
        assert (unrelated / "keep").read_bytes() == b"keep"
    assert not active.exists()


def test_another_materialization_cannot_evict_an_active_reader(tmp_path):
    with materialize(REF, store=Store(), scratch_root=tmp_path) as first:
        with materialize(REF, store=Store(), scratch_root=tmp_path) as second:
            assert first.read_bytes() == second.read_bytes() == RAW
        assert first.read_bytes() == RAW
    assert not first.exists() and not second.exists()
