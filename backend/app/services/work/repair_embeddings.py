"""Immutable accepted embedding batches, private to a repair's frozen input."""
from io import BytesIO
import hashlib
import json
import math
from uuid import uuid4

from sqlalchemy import select

from app.db.models import ArtifactBinding, ArtifactObject, DocumentGeneration
from app.db.session import session_scope
from app.services.artifacts import get_blob_store
from app.services.artifacts.leased import LeasedBlobStore
from app.services.artifacts.publication import canonical_hash
from app.services.artifacts.repository import attach_blob, resolve_blob, _ref
from app.services.generation_artifacts import GenerationIntegrityError


def prefix(work):
    return "repair-embeddings/" + canonical_hash(work.spec.payload) + "/"


def copy_embeddings(session, work, source_id, target_id):
    source = session.get(DocumentGeneration, source_id)
    if source is None or source.work_id != work.work_id or source.doc_id != work.spec.doc_id:
        raise GenerationIntegrityError()
    rows = list(session.scalars(select(ArtifactBinding).where(ArtifactBinding.owner_type == "source",
        ArtifactBinding.owner_id == work.spec.doc_id, ArtifactBinding.generation_id == source_id,
        ArtifactBinding.logical_name.startswith(prefix(work))).order_by(ArtifactBinding.object_id, ArtifactBinding.logical_name)))
    for row in rows:
        blob = session.get(ArtifactObject, row.object_id)
        if blob is None or blob.state != "ready":
            raise GenerationIntegrityError()
        attach_blob(session, ref=_ref(blob), owner_type="source", owner_id=work.spec.doc_id,
            generation_id=target_id, logical_name=row.logical_name)


class RepairEmbedder:
    def __init__(self, embedder, settings, work, generation_id):
        self.embedder, self.settings, self.work, self.generation_id = embedder, settings, work, generation_id

    def embed_texts(self, texts):
        output = []
        for start in range(0, len(texts), 64):
            output.extend(self._batch(list(texts[start:start + 64])))
        return output

    def _check(self, vectors, hashes):
        dimensions = self.settings.embedding_dimensions
        if (not isinstance(vectors, list) or len(vectors) != len(hashes)
                or any(not isinstance(vector, list) or len(vector) != dimensions
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in vector) for vector in vectors)):
            raise GenerationIntegrityError()
        return vectors

    def _batch(self, texts):
        hashes = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]
        signature = {"version": 1, "fingerprint": self.work.spec.payload["embedding_fingerprint"], "hashes": hashes}
        name = prefix(self.work) + canonical_hash(signature)
        with session_scope() as session:
            ref = resolve_blob(session, owner_type="source", owner_id=self.work.spec.doc_id,
                generation_id=self.generation_id, logical_name=name)
        store = get_blob_store(self.settings)
        if ref:
            bound = min(64 * 1024 * 1024, 65536 + 64 * self.settings.embedding_dimensions * 32)
            if ref.size > bound:
                raise GenerationIntegrityError()
            with LeasedBlobStore(store).open(ref) as stream:
                raw = stream.read(bound + 1)
            if len(raw) > bound:
                raise GenerationIntegrityError()
            body = json.loads(raw)
            if set(body) != {*signature, "vectors"} or any(body[key] != value for key, value in signature.items()):
                raise GenerationIntegrityError()
            return self._check(body["vectors"], hashes)
        vectors = self._check(self.embedder.embed_texts(texts), hashes)
        body = json.dumps({**signature, "vectors": vectors}, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ref = store.put(BytesIO(body), object_id=uuid4().hex, media_type="application/json")
        if ref.sha256 != hashlib.sha256(body).hexdigest() or ref.size != len(body):
            raise GenerationIntegrityError()
        with session_scope() as session:
            attach_blob(session, ref=ref, owner_type="source", owner_id=self.work.spec.doc_id,
                generation_id=self.generation_id, logical_name=name)
        return vectors
