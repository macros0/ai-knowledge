import json

import pytest

from app.services.processing_scope_cache import SourceResultCache


SOURCE = {"source_key": "a" * 64, "source_id": "root/0", "content_fingerprint": "b" * 64}


def test_streamed_records_are_lazy_and_filtered_reads_do_not_retain_them(tmp_path,monkeypatch):
    import weakref
    import app.services.processing_scope_cache as module
    cache=SourceResultCache(tmp_path,"1234567890abcdef")
    cache.save_stage(SOURCE,"parse",{"chunks":[]},record_streams={"table_rows":({"n":i} for i in range(600))})
    assert cache.read_stage(SOURCE,"parse",record_kinds={"chunks"})=={"chunks":[]}
    read_json=json.loads
    live=[]
    class Tracked(dict):pass
    def decode(raw):
        row=read_json(raw)
        if row.get("kind")=="table_rows":
            row["value"]=Tracked(row["value"])
            live.append(weakref.ref(row["value"]))
        return row
    monkeypatch.setattr(module.json,"loads",decode)
    count=0
    for value in cache.iter_stage_records(SOURCE,"parse","table_rows"):
        assert value["n"]==count
        assert sum(ref() is not None for ref in live)<=2
        count+=1
    assert count==600


def test_stream_rechecks_digest_if_file_changes_during_iteration(tmp_path):
    cache=SourceResultCache(tmp_path,"1234567890abcdef")
    cache.save_stage(SOURCE,"parse",{"chunks":[{"n":1},{"n":2}]})
    iterator=cache.iter_stage_records(SOURCE,"parse","chunks")
    assert next(iterator)=={"n":1}
    entry=cache._manifest(SOURCE)["stages"]["parse"]
    path=cache.folder(SOURCE)/entry["file"]
    with path.open("ab") as stream:stream.write(b"\n")
    with pytest.raises(ValueError):list(iterator)


def test_completed_zero_concepts_is_cache_hit(tmp_path):
    cache = SourceResultCache(tmp_path, "1234567890abcdef")
    cache.save_stage(SOURCE, "parse", {"chunks": [{"chunk_index": 12, "source_id": "root/0", "content": "old"}]})
    cache.save_stage(SOURCE, "concepts", {"concepts": []})
    hit = cache.inspect(SOURCE)
    assert hit["verified"] and hit["stages"] == {"parse": True, "concepts": True, "embeddings": False}
    assert hit["counts"] == {"chunks": 1, "concepts": 0}
    assert cache.read_stage(SOURCE, "concepts") == {"concepts": []}


def test_toggle_five_times_reuses_every_stage(tmp_path):
    source = {**SOURCE, "embedding_fingerprint": "space-v1"}
    cache = SourceResultCache(tmp_path, "1234567890abcdef")
    vectors = {"vectors": [{"id": "original", "vector": {"dense": [0.125, -0.75], "sparse": {"indices": [1], "values": [0.123456789]}}}],
               "embedding_fingerprint": "space-v1"}
    cache.save_stage(source, "parse", {"chunks": [{"chunk_index": 12, "source_id": "root/0"}]})
    cache.save_stage(source, "concepts", {"concepts": [{"slug": "same", "generated_at": "old"}]})
    cache.save_stage(source, "embeddings", vectors)
    for i in range(5):
        destination = SourceResultCache(tmp_path / str(i), "1234567890abcdef")
        cache.copy_to(source, destination)
        assert all(destination.inspect(source)["stages"].values())
        assert destination.read_stage(source, "embeddings") == vectors
        cache = destination


def test_fingerprint_and_embedding_space_invalidate_only_dependent_stages(tmp_path):
    cache = SourceResultCache(tmp_path, "1234567890abcdef")
    cache.save_stage(SOURCE, "parse", {"chunks": []})
    cache.save_stage(SOURCE, "concepts", {"concepts": []})
    cache.save_stage(SOURCE, "embeddings", {"vectors": [], "embedding_fingerprint": "old"})
    assert not cache.inspect({**SOURCE, "content_fingerprint": "c" * 64})["verified"]
    hit = cache.inspect({**SOURCE, "embedding_fingerprint": "new"})
    assert hit["stages"] == {"parse": True, "concepts": True, "embeddings": False}


def test_partial_manifest_is_not_complete(tmp_path):
    cache = SourceResultCache(tmp_path, "1234567890abcdef")
    cache.save_stage(SOURCE, "parse", {"chunks": []})
    folder = cache.folder(SOURCE)
    manifest = json.loads((folder / "manifest.json").read_text())
    stage = manifest["stages"]["parse"]
    (folder / stage["file"]).write_text("tampered")
    assert not cache.inspect(SOURCE)["stages"]["parse"]
    with pytest.raises(ValueError):
        cache.read_stage(SOURCE, "parse")


def test_failed_write_keeps_last_complete_stage(tmp_path, monkeypatch):
    import app.services.processing_scope_cache as module
    cache = SourceResultCache(tmp_path, "1234567890abcdef")
    artifact = {"chunks": [{"chunk_index": 9, "content": "old"}]}
    cache.save_stage(SOURCE, "parse", artifact)
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(module, "write_json_atomic", fail)
    with pytest.raises(OSError):
        cache.save_stage(SOURCE, "parse", {"chunks": [{"chunk_index": 10}]})
    assert cache.read_stage(SOURCE, "parse") == artifact


def test_new_parse_invalidates_dependent_completion(tmp_path):
    cache = SourceResultCache(tmp_path, "1234567890abcdef")
    cache.save_stage(SOURCE, "parse", {"chunks": [{"content": "v1"}]})
    cache.save_stage(SOURCE, "concepts", {"concepts": []})
    cache.save_stage(SOURCE, "parse", {"chunks": [{"content": "v2"}]})
    assert cache.inspect(SOURCE)["stages"]["concepts"] is False


@pytest.mark.parametrize("source", [{**SOURCE, "source_key": "../escape"}, {**SOURCE, "content_fingerprint": None}])
def test_invalid_identity_never_resolves_storage_path(tmp_path, source):
    cache = SourceResultCache(tmp_path, "1234567890abcdef")
    with pytest.raises(ValueError):
        cache.save_stage(source, "parse", {"chunks": []})


def test_original_is_retained_with_stage_cache_and_copied_after_cancel(tmp_path):
    import hashlib
    path = tmp_path / "original.xlsx"
    path.write_bytes(b"original attachment bytes")
    source = {**SOURCE, "content_fingerprint": hashlib.sha256(path.read_bytes()).hexdigest()}
    cache = SourceResultCache(tmp_path / "staged", "1234567890abcdef")
    cache.save_original(source, path, ".xlsx")
    cache.save_stage(source, "parse", {"chunks": []})
    target = SourceResultCache(tmp_path / "published", "1234567890abcdef")
    cache.copy_to(source, target)
    path.unlink()
    assert target.original_path(source).read_bytes() == b"original attachment bytes"


def test_partial_source_cache_copy_preserves_completion_boundary(tmp_path):
    cache = SourceResultCache(tmp_path / "staged", "1234567890abcdef")
    cache.save_stage(SOURCE, "parse", {"chunks":[{"chunk_index":7}]})
    cache.save_stage(SOURCE, "concepts", {"concepts":[],"completed_chunks":[],"stage_complete":False})
    target = SourceResultCache(tmp_path / "published", "1234567890abcdef")
    cache.copy_to(SOURCE, target)
    assert target.inspect(SOURCE)["stages"] == {"parse":True,"concepts":False,"embeddings":False}
    assert target.read_stage(SOURCE,"concepts",allow_partial=True)["stage_complete"] is False


def test_table_cache_preserves_cell_order_for_existing_row_checksums(tmp_path):
    cache=SourceResultCache(tmp_path,"1234567890abcdef")
    cells={str(index):{"address":str(index)} for index in range(1,15)}
    cache.save_stage(SOURCE,"parse",{"table_rows":[{"row_number":1,"cells":cells}]})
    restored=cache.read_stage(SOURCE,"parse")["table_rows"][0]["cells"]
    assert list(restored)==list(cells)
