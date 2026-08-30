"""Тесты generic мини-справочника attribute_values (module, component, ...)."""
import pytest

from app.db.session import session_scope
from app.db.models import AttributeValue
from app.services.attribute_registry import AttributeRegistry


class TestAttributeRegistry:
    def test_add_and_list(self):
        r = AttributeRegistry()
        r.add("module", "PY")
        assert r.values("module") == ["PY"]

    def test_add_idempotent(self):
        r = AttributeRegistry()
        r.add("module", "PY")
        r.add("module", "PY")
        assert r.values("module") == ["PY"]

    def test_exists(self):
        r = AttributeRegistry()
        r.add("module", "PY")
        assert r.exists("module", "PY")
        assert not r.exists("module", "ZZ")

    def test_remove(self):
        r = AttributeRegistry()
        r.add("module", "PY")
        assert r.remove("module", "PY")
        assert not r.exists("module", "PY")
        assert r.remove("module", "PY") is False

    def test_org_scoped_union_dedupes(self):
        r = AttributeRegistry()
        r.add("module", "PY", org_id=1)
        r.add("module", "PY", org_id=2)
        assert r.values("module", org_id=1) == ["PY"]
        assert r.values("module") == ["PY"]  # union без дублей

    def test_distinct_keys(self):
        r = AttributeRegistry()
        r.add("module", "PY")
        r.add("component", "PY")
        assert r.values("module") == ["PY"]
        assert r.values("component") == ["PY"]
        with session_scope() as s:
            assert s.query(AttributeValue).count() == 2

    def test_unknown_module_rejected_by_development(self):
        from app.services.development_registry import DevelopmentModuleError, get_development_registry

        reg = get_development_registry()
        with pytest.raises(DevelopmentModuleError):
            reg.create("12010", "СЭДО", module="NOT_IN_REF")

    def test_remove_used_module_blocked(self):
        from app.services.attribute_registry import AttributeValueInUseError
        from app.services.development_registry import get_development_registry

        r = AttributeRegistry()
        r.add("module", "PY")
        get_development_registry().create("12010", "СЭДО", module="PY")

        with pytest.raises(AttributeValueInUseError):
            r.remove("module", "PY")
        assert r.exists("module", "PY")

    def test_remove_free_module_allowed(self):
        r = AttributeRegistry()
        r.add("module", "PY")
        assert r.remove("module", "PY") is True
        assert not r.exists("module", "PY")
