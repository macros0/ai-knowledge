"""Verified completed source stages, independent of their active selection."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from app.services.generation_files import _reject_links
from app.services.staging import write_json_atomic

STAGES = ("parse", "concepts", "embeddings")


def embedding_fingerprint(settings):
    return hashlib.sha256(_json([settings.embedding_model, settings.embedding_dimensions,
        settings.qdrant_collection, str(settings.embedding_api_base), "index-text-v1",
        settings.okf_max_concept_chars, settings.okf_max_chunk_index_chars])).hexdigest()


def generation_vector_space(scope, generation_id):
    space = (scope or {}).get("vector_space")
    if space is None:
        return {"kind":"legacy","origin":generation_id}
    if (space.get("kind")=="proven" and re.fullmatch(r"[0-9a-f]{64}",space.get("fingerprint", ""))) or (
        space.get("kind")=="legacy" and re.fullmatch(r"[0-9a-f]{32}",space.get("origin", ""))):
        return dict(space)
    raise ValueError("Invalid generation vector provenance")


def vector_space_fingerprint(space, doc_id):
    return (space["fingerprint"] if space["kind"]=="proven" else
            hashlib.sha256(_json(["legacy-vector-lineage",doc_id,space["origin"]])).hexdigest())


def active_vector_fingerprint(doc_id):
    from app.db.models import DocumentGeneration, DocumentGenerationState
    from app.db.session import session_scope
    with session_scope() as session:
        state=session.get(DocumentGenerationState,doc_id)
        if not state or not state.active_generation_id:
            raise ValueError("No active vector lineage")
        generation=session.get(DocumentGeneration,state.active_generation_id)
        return vector_space_fingerprint(generation_vector_space(generation.processing_scope_json,generation.id),doc_id)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                      default=lambda v: v.isoformat()).encode()


def _hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class SourceResultCache:
    """Caller holds the document writer lock for mutations/copy to a candidate.

    Content addressed stage files precede the atomic completion manifest. An
    interrupted write leaves the last complete version readable. Merely finding
    files, or a partial array of concepts, never establishes completion.
    """
    def __init__(self, root, doc_id):
        if not re.fullmatch(r"[0-9a-f]{16}", doc_id):
            raise ValueError("Invalid cache document")
        self.root, self.doc_id = Path(root).absolute(), doc_id
        _reject_links(self.root)

    def identity(self, source):
        for key in ("source_key", "content_fingerprint"):
            if not re.fullmatch(r"[0-9a-f]{64}", source.get(key) or ""):
                raise ValueError("Unverified cache source identity")
        return hashlib.sha256(_json([self.doc_id, source["source_key"], source["content_fingerprint"], 1])).hexdigest()

    def folder(self, source):
        path = self.root / self.identity(source)
        _reject_links(path)
        return path

    def _manifest(self, source):
        path = self.folder(source) / "manifest.json"
        _reject_links(path)
        if not path.exists():
            return {"schema_version": 1, "doc_id": self.doc_id, "source_key": source["source_key"],
                    "content_fingerprint": source["content_fingerprint"], "stages": {}}
        with path.open("rb") as stream:
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError("Cache manifest exceeds limit")
        manifest = json.loads(raw)
        if (manifest.get("schema_version") != 1 or manifest.get("doc_id") != self.doc_id
                or manifest.get("source_key") != source["source_key"]
                or manifest.get("content_fingerprint") != source["content_fingerprint"]):
            raise ValueError("Cache identity mismatch")
        return manifest

    def _verify_stage(self, source, stage, entry, *, allow_partial=False):
        name = entry.get("file", "")
        if not re.fullmatch(stage + r"-[0-9a-f]{64}\.jsonl", name) or (entry.get("complete") is not True and not allow_partial):
            raise ValueError("Incomplete cache stage")
        path = self.folder(source) / name
        _reject_links(path)
        if _hash(path) != entry.get("sha256"):
            raise ValueError("Cache stage hash mismatch")
        return path

    def inspect(self, source):
        hit = {"verified": False, "cache_id": None, "stages": dict.fromkeys(STAGES, False), "counts": {}}
        try:
            manifest = self._manifest(source)
            hit.update(cache_id=self.identity(source), content_fingerprint=source["content_fingerprint"],
                       manifest_hash=hashlib.sha256(_json(manifest)).hexdigest())
            entries = manifest["stages"]
            for stage in STAGES:
                entry = entries.get(stage)
                if not entry:
                    break
                self._verify_stage(source, stage, entry)
                if stage == "embeddings" and (not source.get("embedding_fingerprint") or
                    entry.get("metadata", {}).get("embedding_fingerprint") != source["embedding_fingerprint"]):
                    break
                hit["stages"][stage] = True
                hit["verified"] = hit["stages"]["parse"]
                hit["counts"].update({k: v for k, v in entry.get("counts", {}).items() if k in {"chunks", "concepts"}})
            hit.update(verified=hit["stages"]["parse"], cache_id=self.identity(source),
                       content_fingerprint=source["content_fingerprint"],
                       manifest_hash=hashlib.sha256(_json(manifest)).hexdigest())
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return hit

    def save_stage(self, source, stage, artifact, *, record_streams=None):
        if stage not in STAGES:
            raise ValueError("Unknown cache stage")
        folder = self.folder(source)
        folder.mkdir(parents=True, exist_ok=True)
        manifest = self._manifest(source)
        for previous in STAGES[:STAGES.index(stage)]:
            self._verify_stage(source, previous, manifest["stages"].get(previous, {}))
        counts, metadata = {}, {}
        record_streams=record_streams or {}
        if set(record_streams)&set(artifact):
            raise ValueError("Duplicate cache record stream")
        streams=[(kind,values) for kind,values in artifact.items() if isinstance(values,list)]
        streams.extend(record_streams.items())
        metadata={kind:value for kind,value in artifact.items() if not isinstance(value,list)}
        temporary = folder / (uuid.uuid4().hex + ".tmp")
        digest = hashlib.sha256()
        try:
            with temporary.open("xb") as stream:
                for kind, values in streams:
                    counts[kind] = 0
                    for value in values:
                        # Row checksums preserve the parser's numeric cell
                        # order. Sorting string keys moves 10 before 2.
                        raw = json.dumps({"kind":kind,"value":value},ensure_ascii=True,separators=(",",":"),
                            default=lambda v:v.isoformat()).encode()+b"\n"
                        stream.write(raw)
                        digest.update(raw)
                        counts[kind]+=1
                stream.flush()
                os.fsync(stream.fileno())
            fingerprint = digest.hexdigest()
            name = f"{stage}-{fingerprint}.jsonl"
            target = folder / name
            _reject_links(target)
            os.replace(temporary, target)
            new_entry = {"file": name, "sha256": fingerprint, "complete": artifact.get("stage_complete", True) is True,
                         "counts": counts, "metadata": metadata}
            old_entry = manifest["stages"].get(stage)
            manifest["stages"][stage] = new_entry
            if old_entry != new_entry:
                for dependent in STAGES[STAGES.index(stage) + 1:]:
                    manifest["stages"].pop(dependent, None)
            write_json_atomic(folder / "manifest.json", manifest)
            return self.inspect(source)
        finally:
            temporary.unlink(missing_ok=True)

    def read_stage(self, source, stage, *, allow_partial=False, record_kinds=None, omit_records=()):
        if stage not in STAGES:
            raise ValueError("Unknown cache stage")
        entry = self._manifest(source)["stages"].get(stage, {})
        if record_kinds is None:record_kinds=set(entry.get("counts",{}))-set(omit_records)
        result = dict(entry.get("metadata", {}))
        result.update({key: [] for key in entry.get("counts", {}) if record_kinds is None or key in record_kinds})
        for kind,value in self._iter_stage(source,stage,entry,allow_partial=allow_partial,record_kinds=record_kinds):
            result[kind].append(value)
        return result

    def iter_stage_records(self,source,stage,kind,*,allow_partial=False):
        if stage not in STAGES:raise ValueError("Unknown cache stage")
        entry=self._manifest(source)["stages"].get(stage,{})
        for _,value in self._iter_stage(source,stage,entry,allow_partial=allow_partial,record_kinds={kind}):
            yield value

    def _iter_stage(self,source,stage,entry,*,allow_partial=False,record_kinds=None):
        path=self._verify_stage(source,stage,entry,allow_partial=allow_partial)
        counts=dict.fromkeys(entry.get("counts",{}),0)
        digest=hashlib.sha256()
        with path.open("rb") as stream:
            for raw in stream:
                digest.update(raw)
                row = json.loads(raw)
                if row.get("kind") not in counts:
                    raise ValueError("Invalid cached record type")
                kind=row["kind"]
                counts[kind]+=1
                if record_kinds is None or kind in record_kinds:
                    yield kind,row["value"]
        if digest.hexdigest()!=entry.get("sha256"):
            raise ValueError("Cache stage hash mismatch")
        if counts!=entry.get("counts",{}):
            raise ValueError("Cache count mismatch")

    def save_original(self, source, path, extension):
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
            raise ValueError("Invalid original extension")
        path = Path(path)
        _reject_links(path)
        if path.stat().st_size > 50 * 1024 * 1024 or _hash(path) != source["content_fingerprint"]:
            raise ValueError("Original fingerprint or size changed")
        folder = self.folder(source)
        folder.mkdir(parents=True, exist_ok=True)
        manifest = self._manifest(source)
        name = "original" + extension
        target, temporary = folder / name, folder / (uuid.uuid4().hex + ".tmp")
        _reject_links(target)
        try:
            shutil.copyfile(path, temporary)
            if _hash(temporary) != source["content_fingerprint"]:
                raise ValueError("Copied original changed")
            os.replace(temporary, target)
            manifest["original"] = {"file": name, "sha256": source["content_fingerprint"]}
            write_json_atomic(folder / "manifest.json", manifest)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def original_path(self, source):
        original = self._manifest(source).get("original")
        if not original or not re.fullmatch(r"original\.[a-z0-9]{1,8}", original.get("file", "")):
            raise ValueError("Cached original unavailable")
        path = self.folder(source) / original["file"]
        _reject_links(path)
        if _hash(path) != source["content_fingerprint"] or original["sha256"] != source["content_fingerprint"]:
            raise ValueError("Cached original changed")
        return path

    def copy_to(self, source, destination):
        manifest = self._manifest(source)
        if not manifest["stages"]:
            raise ValueError("No completed source stages")
        folder = destination.folder(source)
        folder.mkdir(parents=True, exist_ok=True)
        incomplete = False
        for stage in STAGES:
            entry = manifest["stages"].get(stage)
            if not entry:
                incomplete = True
                continue
            if incomplete:
                raise ValueError("Dependent cache stage follows an incomplete stage")
            source_path = self._verify_stage(source, stage, entry, allow_partial=True)
            incomplete = entry.get("complete") is not True
            target = folder / entry["file"]
            _reject_links(target)
            if source_path.resolve() != target.resolve():
                shutil.copyfile(source_path, target)
            if _hash(target) != entry["sha256"]:
                raise ValueError("Copied cache stage differs")
        if manifest.get("original"):
            original = self.original_path(source)
            target = folder / manifest["original"]["file"]
            _reject_links(target)
            if target.resolve() != original.resolve():
                shutil.copyfile(original, target)
            if _hash(target) != source["content_fingerprint"]:
                raise ValueError("Copied cached original changed")
        write_json_atomic(folder / "manifest.json", manifest)
        return destination.inspect(source)


def _document_cache(doc_id):
    from app.config import get_settings
    return SourceResultCache(get_settings().uploads_dir / doc_id / "scope-stage-cache", doc_id)


def assert_full_regeneration_cache_safe(session, settings, doc_id, selection):
    """Refuse reset while inactive stable IDs cannot safely be carried forward.

    A full rebuild assigns new canonical indices. Scope updates preserve IDs;
    silently retaining the old cache beside a full rebuild could collide later.
    """
    from app.db.models import Document, DocumentGeneration, DocumentGenerationState
    from app.services.generation_files import generation_paths
    from app.services.errors import ConflictError
    state=session.get(DocumentGenerationState,doc_id)
    if not state or not state.active_generation_id:
        return
    generation=session.get(DocumentGeneration,state.active_generation_id)
    effective=selection or generation.processing_scope_json or {}
    document=session.get(Document,doc_id)
    supported={node["source_key"] for node in effective.get("inventory",{}).get("nodes",[])
               if node.get("supported") is True}
    if effective.get("source_file_hash")==document.file_hash and any(
        not item.get("effective_included") and item["source_key"] in supported
        for item in effective.get("decisions",[])
    ):
        raise ConflictError("Полная регенерация может потерять кэш выключенных вложений. Используйте изменение состава.",
                            code="scope_cache_preservation_required")
    desired={d["source_key"] for d in (selection or generation.processing_scope_json or {}).get("decisions",[])
             if d.get("effective_included")}
    roots=[generation_paths(settings,doc_id,state.active_generation_id).bundle/"source-cache",
           settings.uploads_dir/doc_id/"scope-stage-cache"]
    for root in roots:
        _reject_links(root)
        if not root.exists():
            continue
        for entry in root.iterdir():
            _reject_links(entry)
            manifest=entry/"manifest.json"
            _reject_links(manifest)
            if not manifest.is_file():
                continue
            with manifest.open("rb") as stream:
                raw=stream.read(256*1024+1)
            try:
                metadata=json.loads(raw) if len(raw)<=256*1024 else {}
            except (ValueError,TypeError):
                metadata={}
            if metadata.get("source_key") not in desired:
                raise ConflictError("Полная регенерация может потерять кэш выключенных вложений. Сначала включите их или применяйте изменение состава.",
                                    code="scope_cache_preservation_required")


def inspect_source_cache(doc_id, source):
    from app.config import get_settings
    from app.db.models import DocumentGenerationState
    from app.db.session import session_scope
    from app.services.generation_files import generation_paths
    settings = get_settings()
    source = {**source, "embedding_fingerprint": active_vector_fingerprint(doc_id)}
    candidates = [_document_cache(doc_id)]
    with session_scope() as session:
        state = session.get(DocumentGenerationState, doc_id)
        if state and state.active_generation_id:
            candidates.append(SourceResultCache(generation_paths(settings, doc_id, state.active_generation_id).bundle / "source-cache", doc_id))
    best = {"verified": False, "stages": dict.fromkeys(STAGES, False)}
    for cache in candidates:
        hit = cache.inspect(source)
        if hit.get("verified") and sum(hit["stages"].values()) > sum(best["stages"].values()):
            best = {**hit, "location": str(cache.root)}
    return best


def save_completed_source_stage(doc_id, source, stage, artifact):
    return _document_cache(doc_id).save_stage(source, stage, artifact)


def copy_source_cache(doc_id, cache_id, generation_id):
    from app.config import get_settings
    from app.services.generation_files import generation_paths
    if not re.fullmatch(r"[0-9a-f]{64}", cache_id or ""):
        raise ValueError("Invalid cache identity")
    cache = _document_cache(doc_id)
    manifest_path = cache.root / cache_id / "manifest.json"
    _reject_links(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = {key: manifest[key] for key in ("source_key", "content_fingerprint")}
    destination = SourceResultCache(generation_paths(get_settings(), doc_id, generation_id).bundle / "source-cache", doc_id)
    return cache.copy_to(source, destination)
