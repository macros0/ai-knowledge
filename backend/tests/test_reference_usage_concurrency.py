from app.db.models import Tag
from app.db.session import session_scope
from app.services.registry import get_registry
from app.services.tag_registry import TagRegistry


def test_assignment_rechecks_tag_deleted_after_id_resolution(monkeypatch):
    original = TagRegistry.get_or_create_ids
    def resolve_then_cleanup(self, values, *args, **kwargs):
        identifiers = original(self, values, *args, **kwargs)
        self.delete("between-resolution-and-link")
        return identifiers
    monkeypatch.setattr(TagRegistry, "get_or_create_ids", resolve_then_cleanup)
    document = get_registry().create("1234123412341234", "test.txt", "text/plain", 1,
                                    tags=["between-resolution-and-link"])
    assert document["tags"] == ["between-resolution-and-link"]
    with session_scope() as s:
        assert s.query(Tag).filter_by(canonical_text="between-resolution-and-link").one().deleted_at is None


def test_restore_reconciles_soft_deleted_tags_in_its_write_transaction():
    registry = get_registry()
    registry.create("1234123412341234", "test.txt", "text/plain", 1, tags=["restore-tag"])
    registry.soft_delete("1234123412341234", "tester")
    TagRegistry().delete("restore-tag")
    assert registry.restore("1234123412341234")
    with session_scope() as s:
        assert s.query(Tag).filter_by(canonical_text="restore-tag").one().deleted_at is None
