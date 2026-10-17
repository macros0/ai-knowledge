from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from app.services.vector_store import VectorStore, concept_point_id
from app.services.generation_artifacts import GenerationIntegrityError


DOC, OLD, NEW = "1234567890abcdef", "a" * 32, "b" * 32


def fixture_points(count=2):
    store = VectorStore.__new__(VectorStore)
    store.settings = SimpleNamespace(qdrant_collection="test-only")
    points, mapping = {}, {}
    for i in range(count):
        slug = f"slug-{i}"
        old = concept_point_id(DOC, slug, generation_id=OLD)
        identity = {"point_type": "concept", "slug": slug, "source_id": "root/0", "chunk_index": i}
        points[old] = SimpleNamespace(id=old, payload={**identity, "doc_id": DOC, "generation_id": OLD},
            vector={"": [0.5, -0.125], "sparse": {"indices": [1, 42], "values": [0.3, 0.8]}})
        mapping[old] = {"id": concept_point_id(DOC, slug, generation_id=NEW), "identity": identity}
    store.client = Mock()
    store.client.retrieve.side_effect = lambda **kw: [points[k] for k in kw["ids"] if k in points]
    store._upsert_batches = Mock(return_value={"failed_batches": 0})
    return store, points, mapping


def test_copy_preserves_dense_and_sparse():
    store, points, mapping = fixture_points(257)
    count = store.copy_generation_points(DOC, OLD, NEW, mapping)
    assert count == 257
    assert max(len(c.kwargs["ids"]) for c in store.client.retrieve.call_args_list) <= 256
    written = [p for c in store._upsert_batches.call_args_list for p in c.args[0]]
    assert {str(p.id) for p in written} == {v["id"] for v in mapping.values()}
    for point in written:
        assert point.vector[""] == [0.5, -0.125]
        sparse = point.vector["sparse"]
        assert sparse.indices == [1, 42] and sparse.values == [0.3, 0.8]
        assert point.payload["generation_id"] == NEW
    assert all(p.payload["generation_id"] == OLD for p in points.values())


@pytest.mark.parametrize("corrupt", ["missing", "foreign", "identity", "vectors", "duplicate"])
def test_missing_foreign_or_partial_vectors_prevent_publish(corrupt):
    store, points, mapping = fixture_points()
    first = next(iter(points))
    if corrupt == "missing":
        points.pop(first)
    elif corrupt == "foreign":
        points[first].payload["doc_id"] = "another"
    elif corrupt == "identity":
        points[first].payload["source_id"] = "root/other"
    elif corrupt == "vectors":
        points[first].vector.pop("sparse")
    else:
        values = list(mapping.values())
        values[1]["id"] = values[0]["id"]
    with pytest.raises((ValueError, GenerationIntegrityError)):
        store.copy_generation_points(DOC, OLD, NEW, mapping)
    store._upsert_batches.assert_not_called()


def test_target_id_must_be_derived_from_canonical_identity():
    store, _, mapping = fixture_points()
    next(iter(mapping.values()))["id"] = "0" * 32
    with pytest.raises(ValueError):
        store.copy_generation_points(DOC, OLD, NEW, mapping)
    store._upsert_batches.assert_not_called()


def test_fragment_group_is_validated_in_each_generation_without_changing_vectors():
    from app.services.chunk_index_projection import (build_chunk_index_projection,
        chunk_fragment_point_id_from_descriptor, chunk_fragment_group_id)
    store, _, _ = fixture_points(0)
    part = next(build_chunk_index_projection("Title", "body " * 1500, version=1).fragments())
    descriptor = part.descriptor
    assert descriptor is not None
    identity = {"point_type":"chunk", "source_id":"root/0", "chunk_index":5, "chunk_fragment":descriptor,
                "chunk_fragment_group":chunk_fragment_group_id(DOC, NEW, "root/0", 5)}
    old = chunk_fragment_point_id_from_descriptor(DOC, 5, OLD, "root/0", descriptor)
    new = chunk_fragment_point_id_from_descriptor(DOC, 5, NEW, "root/0", descriptor)
    vector = {"": [0.5, -0.125], "sparse": {"indices":[1], "values":[0.3]}}
    point = SimpleNamespace(id=old, vector=vector, payload={**identity, "doc_id":DOC, "generation_id":OLD,
        "chunk_fragment_group":chunk_fragment_group_id(DOC, OLD, "root/0", 5)})
    store.client.retrieve.return_value = [point]
    store.client.retrieve.side_effect = None
    assert store.copy_generation_points(DOC, OLD, NEW, {old:{"id":new,"identity":identity}}) == 1
    copied = store._upsert_batches.call_args.args[0][0]
    assert copied.payload["chunk_fragment_group"] == identity["chunk_fragment_group"]
    assert copied.vector[""] == vector[""]
