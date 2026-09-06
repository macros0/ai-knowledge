"""Тесты переводов справочников (Этап 7 фаза B): set_translation, display,
bulk_review, backfill (off-провайдер с ручным словарём, идемпотентность)."""
from app.services.tag_registry import TagRegistry
from app.services.translation import backfill_reference_data


class _User:
    user_id = "u-admin"
    username = "demo.admin"


class TestTagTranslations:
    def test_set_translation_and_display(self):
        reg = TagRegistry()
        reg.add(["расчёт зарплаты"])
        tag_id = next(t["id"] for t in reg.all() if t["name"] == "расчёт зарплаты")
        reg.set_translation(tag_id, "en", "payroll", is_machine=True)
        items = {t["name"]: t for t in reg.all(locale="en")}
        assert items["расчёт зарплаты"]["display"] == "payroll"
        assert items["расчёт зарплаты"]["needs_review"] is True

    def test_bulk_review_marks_reviewed(self):
        reg = TagRegistry()
        reg.add(["расчёт"])
        tag_id = next(t["id"] for t in reg.all())
        reg.set_translation(tag_id, "en", "payroll", is_machine=True)
        assert reg.bulk_review([tag_id], "demo.editor") == 1
        assert next(t for t in reg.all() if t["id"] == tag_id)["needs_review"] is False


class TestBackfill:
    def test_backfill_with_manual_translations(self):
        reg = TagRegistry()
        reg.add(["расчёт зарплаты", "СЭДО"])
        result = backfill_reference_data(
            "en", ["tags"], translations={"расчёт зарплаты": "payroll", "СЭДО": "SEDO"}, user=_User()
        )
        assert result["tags"]["created"] == 2
        items = {t["name"]: t for t in reg.all(locale="en")}
        assert items["расчёт зарплаты"]["display"] == "payroll"
        assert items["СЭДО"]["display"] == "SEDO"

    def test_backfill_idempotent_respects_reviewed(self):
        reg = TagRegistry()
        reg.add(["расчёт"])
        tag_id = next(t["id"] for t in reg.all())
        reg.set_translation(tag_id, "en", "payroll", is_machine=False, reviewed_by="demo.editor")
        # Ручной перевод не перезаписывается: с пустым словарём created == 0.
        result = backfill_reference_data("en", ["tags"], translations={}, user=_User())
        assert result["tags"]["created"] == 0
        items = {t["name"]: t for t in reg.all(locale="en")}
        assert items["расчёт"]["display"] == "payroll"
