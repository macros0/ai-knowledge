import copy

import pytest

from app.db.models import Document, DocumentProcessingReview
from app.db.session import session_scope
from app.services.processing_scope import (
    create_review,
    save_review_selection,
    snapshot_from_review,
    ProcessingScopeConflict,
)
from docparser.attachment_policy import AttachmentInventory, AttachmentNode


DOC = "1234567890abcdef"


def make_review():
    with session_scope() as session:
        session.add(Document(id=DOC, filename="decision.docx", status="awaiting_attachments"))
        session.flush()
        inv = AttachmentInventory(
            source_file_hash="a" * 64,
            nodes=[
                AttachmentNode(
                    "1" * 64,
                    "root",
                    "ooxml:one",
                    "one.xlsx",
                    "xlsx",
                    metrics={"rows": 500},
                    assessment="heavy",
                ),
                AttachmentNode(
                    "2" * 64, "root", "ooxml:two", "two.xlsx", "xlsx", metrics={"rows": 2}
                ),
            ],
        )
        review = create_review(session, DOC, inv, base_generation_id=None, actor="editor")
        return review.id, review.revision


def test_aggregate_recommendation_survives_user_save_and_legacy_is_unknown():
    from app.services.processing_scope import scope_summary
    from docparser.attachment_policy import AssessmentLimits

    with session_scope() as session:
        session.add(Document(id=DOC, filename="decision.docx", status="awaiting_attachments"))
        session.flush()
        inventory = AttachmentInventory(source_file_hash="a" * 64, nodes=[
            AttachmentNode(str(i) * 64, "root", str(i), f"{i}.xlsx", "xlsx", metrics={"rows": 10})
            for i in (1, 2)
        ])
        row = create_review(session, DOC, inventory, base_generation_id=None, actor="editor",
                            limits=AssessmentLimits(total_rows=15))
        before = copy.deepcopy(snapshot_from_review(row))
        assert row.summary_json["recommended_excluded_count"] == 1
        row = save_review_selection(session, DOC, row.id, row.revision,
                                    frozenset(n.source_key for n in inventory.nodes), actor="editor")
        after = snapshot_from_review(row)
        assert row.summary_json["excluded"] == 0
        assert row.summary_json["recommended_excluded_count"] == 1
        assert after["inventory"]["recommendations"] == before["inventory"]["recommendations"]
        assert any(d["reason"] == "aggregate_budget" for d in after["inventory"]["recommendations"])
        legacy = copy.deepcopy(after)
        del legacy["inventory"]["recommendations"]
        assert scope_summary(legacy)["recommended_excluded_count"] is None
        assert legacy["selection_hash"] == after["selection_hash"]


def test_review_retains_recommendation_and_source_hash():
    rid, version = make_review()
    with session_scope() as session:
        row = session.get(DocumentProcessingReview, DOC)
        snapshot = snapshot_from_review(row)
        assert row.id == rid and row.revision == version
        assert snapshot["source_file_hash"] == "a" * 64
        assert [d["effective_included"] for d in snapshot["decisions"]] == [False, True]


def test_explicit_selection_preserves_heavy_recommendation_and_cas():
    rid, version = make_review()
    with session_scope() as session:
        row = save_review_selection(
            session, DOC, rid, version, frozenset({"1" * 64}), actor="second-editor"
        )
        first = copy.deepcopy(snapshot_from_review(row))
        assert row.revision == version + 1
        assert first["decisions"][0]["origin"] == "user"
        assert first["inventory"]["nodes"][0]["assessment"] == "heavy"
    with pytest.raises(ProcessingScopeConflict):
        with session_scope() as session:
            save_review_selection(session, DOC, rid, version, frozenset(), actor="editor")
    with session_scope() as session:
        assert snapshot_from_review(session.get(DocumentProcessingReview, DOC)) == first


def test_unknown_or_foreign_key_is_not_silently_selected():
    rid, version = make_review()
    with pytest.raises(ProcessingScopeConflict):
        with session_scope() as session:
            save_review_selection(session, DOC, rid, version, frozenset({"f" * 64}), actor="editor")


def test_snapshot_detached_from_mutable_review():
    rid, version = make_review()
    with session_scope() as session:
        old = snapshot_from_review(session.get(DocumentProcessingReview, DOC))
        new = save_review_selection(session, DOC, rid, version, frozenset(), actor="editor")
        assert old["selection_hash"] != snapshot_from_review(new)["selection_hash"]
        assert old["decisions"][1]["effective_included"]


def test_selected_child_requires_selected_ancestor():
    rid, version = make_review()
    with session_scope() as session:
        row = session.get(DocumentProcessingReview, DOC)
        data = copy.deepcopy(row.inventory_json)
        data["nodes"][1]["parent_key"] = "1" * 64
        row.inventory_json = data
    with pytest.raises(ProcessingScopeConflict):
        with session_scope() as session:
            save_review_selection(session, DOC, rid, version, frozenset({"2" * 64}), actor="editor")


def test_reassessment_preserves_explicit_selection_and_keeps_new_children_off():
    from app.services.processing_scope import refresh_review_inventory

    rid, version = make_review()
    with session_scope() as session:
        row = save_review_selection(
            session, DOC, rid, version, frozenset({"1" * 64}), actor="editor"
        )
        inventory = AttachmentInventory.from_dict(row.inventory_json)
        inventory.nodes.append(AttachmentNode("3" * 64, "root", "new", "new.xlsx", "xlsx"))
        row = refresh_review_inventory(session, DOC, rid, row.revision, inventory, actor="editor")
        assert [d["effective_included"] for d in row.selection_json] == [True, False, False]
        assert row.selection_json[-1]["reason"] == "new_source"


def test_hard_delete_removes_review_with_sqlite_foreign_keys_off():
    make_review()
    from app.services.registry import DocumentRegistry

    DocumentRegistry().delete(DOC)
    with session_scope() as session:
        assert session.get(DocumentProcessingReview, DOC) is None


def test_prefill_excludes_known_child_of_unknown_parent():
    from app.services.processing_scope import eligible_existing_selection

    inventory = AttachmentInventory(
        source_file_hash="a" * 64,
        nodes=[
            AttachmentNode(
                "1" * 64,
                "root",
                "one",
                "container.zip",
                "archive",
                assessment="unknown",
                children_complete=False,
            ),
            AttachmentNode("2" * 64, "1" * 64, "two", "known.xlsx", "xlsx"),
        ],
    )
    assert eligible_existing_selection(inventory, {"1" * 64, "2" * 64}) == frozenset()


def test_late_create_cannot_replace_another_editors_saved_draft():
    rid, revision = make_review()
    with session_scope() as session:
        saved = save_review_selection(
            session, DOC, rid, revision, frozenset(), actor="other-editor"
        )
        inventory = AttachmentInventory.from_dict(saved.inventory_json)
        expected = snapshot_from_review(saved)
    # Simulates a second POST finishing its preflight after the first editor saved.
    with pytest.raises(ProcessingScopeConflict):
        with session_scope() as session:
            create_review(session, DOC, inventory, base_generation_id=None, actor="late-editor")
    with session_scope() as session:
        assert snapshot_from_review(session.get(DocumentProcessingReview, DOC)) == expected


@pytest.mark.parametrize("status", ["uploaded", "queued", "processing", "splitting", "indexing"])
def test_review_final_lock_rejects_concurrent_upload_or_processing(status):
    with session_scope() as session:
        session.add(Document(id=DOC, filename="light.docx", status=status))
    with pytest.raises(ProcessingScopeConflict):
        with session_scope() as session:
            create_review(
                session,
                DOC,
                AttachmentInventory(source_file_hash="a" * 64),
                base_generation_id=None,
                actor="late-editor",
            )
    with session_scope() as session:
        assert session.get(Document, DOC).status == status
        assert session.get(DocumentProcessingReview, DOC) is None
