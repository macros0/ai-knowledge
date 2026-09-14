import json
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_glossary_rule_and_merge_keys_are_in_backend_manifests():
    keys = json.loads((ROOT / "backend/app/i18n/ui_keys.json").read_text(encoding="utf-8"))
    english = json.loads((ROOT / "backend/app/i18n/ui_en.json").read_text(encoding="utf-8"))
    expected = {
        "admin.glossary.rulesHint",
        "admin.glossary.ruleName",
        "admin.glossary.rulePrefixes",
        "admin.glossary.addRule",
        "admin.glossary.editRule",
        "admin.glossary.saveRule",
        "admin.glossary.deleteRule",
        "admin.glossary.mergeTitle",
        "admin.glossary.mergePreview",
        "admin.glossary.mergeCommit",
        "admin.glossary.kind.sap_program",
        "admin.glossary.kind.sap_object",
    }
    assert expected <= keys.keys()
    assert expected <= english.keys()
    assert "reindex" in english["admin.glossary.infotypeRuleDescription"].lower()


def test_frontend_locales_have_the_same_glossary_contract_keys():
    for filename in ("ru.js", "en.js"):
        text = (ROOT / "frontend/src/i18n/locales" / filename).read_text(encoding="utf-8")
        assert '"admin.glossary.editRule"' in text
        assert '"admin.glossary.mergeTitle"' in text
        assert '"admin.glossary.mergeField.original_name"' in text
