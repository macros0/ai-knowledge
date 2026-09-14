"""Pure, validated merge proposals shared by preview and commit."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from app import error_codes as codes
from app.services.glossary.forms import forms_for_number

from app.services.glossary.normalization import (
    GlossaryValidationError, GlossaryMultipleInfotypeNumbersError, normalize_alias, validate_alias_options, validate_locale,
)


class GlossaryMergeConflictError(ValueError):
    """The proposed merge needs a compatible identity or explicit field choice."""
    code = codes.GLOSSARY_IDENTITY_CONFLICT


class GlossaryMergeChoicesRequiredError(GlossaryMergeConflictError):
    code = codes.GLOSSARY_MERGE_CHOICES_REQUIRED


def preview_digest(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def merge_request_hash(request: dict, digest: str) -> str:
    """Bind a replay receipt to every semantic field, not just a client digest."""
    return preview_digest({
        "source": request.get("source_term_id", request.get("source_id")),
        "target": request.get("target_term_id", request.get("target_id")),
        "source_version": request.get("source_version"),
        "target_version": request.get("target_version"),
        "revision": request.get("expected_revision"),
        "selections": request.get("selections") or {},
        "draft": request.get("draft"),
        "source_edit": request.get("source_edit"),
        "digest": digest,
    })


_SOURCE_FIELDS = ("original_name", "original_description", "canonical_locale")
_SELECT_FIELDS = (*_SOURCE_FIELDS, "kind", "infotype_number", "enabled")


def _has_value(value) -> bool:
    return value is not None and value != ""


def unresolved_merge_fields(source: dict, target: dict, selections: dict | None = None) -> list[str]:
    selections = selections or {}
    result = [field for field in _SELECT_FIELDS
            if field not in selections and _has_value(source.get(field)) and _has_value(target.get(field))
            and source[field] != target[field]]
    translated_choices = _choices(selections, 'translation_choices', 'locale')
    source_locales = {item['locale'] for item in source.get('translations') or []}
    target_locales = {item['locale'] for item in target.get('translations') or []}
    result.extend(f'translations[{locale}]' for locale in sorted(source_locales & target_locales)
                  if locale not in translated_choices)
    alias_choices = _choices(selections, 'alias_choices', 'normalized_alias')
    result_name = source.get('original_name') if selections.get('original_name') == 'source' or not target.get('original_name') else target.get('original_name')
    name_key = normalize_alias(result_name or '')
    aliases = {}
    for owner in (target, source):
        for alias in owner.get('aliases') or []:
            key = normalize_alias(alias['alias'])
            existing = aliases.get(key)
            if existing and key != name_key and key not in alias_choices and any(
                existing.get(field) != alias.get(field) for field in ('locale', 'auto_expand', 'search_enabled')
            ):
                result.append(f'aliases[{key}]')
            aliases[key] = alias
    return list(dict.fromkeys(result))


def merge_proposal_digest(source, target, selections, merged, revision, draft=None):
    return preview_digest(dict(source=source, target=target, selections=selections,
                               merged=merged, revision=int(revision), draft=draft))


def _name_permissions(name: str, kind: str) -> bool:
    try:
        validate_alias_options(name, kind=kind, auto_expand=True, search_enabled=True)
    except GlossaryValidationError:
        return False
    return True


def _choices(selections: dict, name: str, key: str) -> dict:
    values = selections.get(name, [])
    if not isinstance(values, list) or any(not isinstance(value, dict) or key not in value for value in values):
        raise GlossaryMergeConflictError(f"Некорректный выбор {name}")
    result = {}
    for value in values:
        item_key = value[key]
        if not isinstance(item_key, str) or item_key in result:
            raise GlossaryMergeConflictError(f"Повторный или некорректный выбор {name}")
        result[item_key] = value
    return result


def build_merge_result(source: dict, target: dict, selections: dict | None = None, rules=(), *, allow_unresolved: bool = False) -> dict:
    """Compute the exact saved fields, aliases and translation freshness."""
    selections = selections or {}
    if set(selections) - {*_SELECT_FIELDS, "alias_choices", "translation_choices"}:
        raise GlossaryMergeConflictError("Неизвестное поле выбора объединения")
    for field in _SELECT_FIELDS:
        if field in selections and selections[field] not in ("source", "target"):
            raise GlossaryMergeConflictError(f"Выберите source или target для {field}")
    numbers = {term.get("infotype_number") for term in (source, target) if term.get("infotype_number")}
    if len(numbers) > 1:
        raise GlossaryMultipleInfotypeNumbersError("Нельзя объединять разные номера инфотипов")
    unresolved = unresolved_merge_fields(source, target, selections)
    if unresolved and not allow_unresolved:
        raise GlossaryMergeChoicesRequiredError("Выберите итоговые значения: " + ", ".join(unresolved))

    result = deepcopy(target)
    for field in _SELECT_FIELDS:
        if selections.get(field) == "source" or (field not in selections and not _has_value(target.get(field))):
            result[field] = source.get(field)
    if allow_unresolved and 'kind' in unresolved and numbers:
        result['kind'] = 'sap_infotype'
    if numbers and (result.get("kind") != "sap_infotype" or result.get("infotype_number") not in numbers):
        raise GlossaryMergeConflictError("Объединение не должно удалять номер инфотипа")
    if result.get("kind") == "sap_infotype" and not result.get("infotype_number"):
        raise GlossaryMergeConflictError("Для инфотипа необходим номер")

    source_changed = any(result.get(field) != target.get(field) for field in _SOURCE_FIELDS)
    result["source_revision"] = target.get("source_revision", 1) + int(source_changed)
    result["version"] = target.get("version", 1) + 1
    name_key = normalize_alias(result.get("original_name", ""))
    rule_forms = {form.normalized for form in forms_for_number(result.get('infotype_number'), tuple(rules))}
    aliases: dict[str, dict] = {}
    alias_choices = _choices(selections, "alias_choices", "normalized_alias")
    for owner in (target, source):
        for alias in owner.get("aliases") or []:
            key = normalize_alias(alias["alias"])
            if key == name_key:
                continue
            existing = aliases.get(key)
            if not allow_unresolved and existing and any(existing.get(field) != alias.get(field) for field in ("locale", "auto_expand", "search_enabled")) and key not in alias_choices:
                raise GlossaryMergeConflictError(f"Выберите язык и флаги алиаса: {alias['alias']}")
            aliases.setdefault(key, deepcopy(alias))
        name = owner.get("original_name", "")
        key = normalize_alias(name)
        if key and key != name_key and key not in aliases and key not in rule_forms:
            allowed = _name_permissions(name, result["kind"])
            aliases[key] = dict(alias=name, normalized_alias=key, locale=owner.get("canonical_locale"),
                                auto_expand=allowed, search_enabled=allowed)
    if set(alias_choices) - set(aliases):
        raise GlossaryMergeConflictError("Выбран отсутствующий алиас")
    for key, choice in alias_choices.items():
        if set(choice) != {"normalized_alias", "locale", "auto_expand", "search_enabled"}:
            raise GlossaryMergeConflictError("Выбор алиаса должен содержать язык и оба флага")
        if not isinstance(choice["auto_expand"], bool) or not isinstance(choice["search_enabled"], bool):
            raise GlossaryMergeConflictError("Флаги алиаса должны быть логическими")
        aliases[key].update(choice)
    for alias in aliases.values():
        if alias.get("locale") == "und":
            alias["locale"] = None
        if alias.get("locale") is not None:
            validate_locale(alias["locale"], allow_und=False)
        validate_alias_options(alias["alias"], kind=result["kind"],
                               auto_expand=alias.get("auto_expand", False), search_enabled=alias.get("search_enabled", False))
    result["aliases"] = list(aliases.values())

    choices = _choices(selections, "translation_choices", "locale")
    owners = {}
    for owner in (target, source):
        for translation in owner.get("translations") or []:
            owners.setdefault(translation["locale"], []).append((owner, translation))
    if set(choices) - set(owners):
        raise GlossaryMergeConflictError("Выбран отсутствующий перевод")
    translations = []
    for locale, candidates in sorted(owners.items()):
        choice = choices.get(locale)
        if not allow_unresolved and len(candidates) > 1 and choice is None:
            raise GlossaryMergeConflictError(f"Выберите перевод для языка {locale}")
        if choice:
            selected = next(((owner, value) for owner, value in candidates if owner["id"] == choice.get("from_term_id")), None)
            if selected is None or selected[1]["version"] != choice.get("expected_version"):
                raise GlossaryMergeConflictError(f"Выбор перевода {locale} устарел")
            owner, value = selected
        else:
            owner, value = candidates[0]
        translated = deepcopy(value)
        # The same revision number on two cards says nothing about source text.
        source_equal = all(owner.get(field) == result.get(field) for field in _SOURCE_FIELDS)
        was_current = value["source_revision"] == owner.get("source_revision", 1)
        if source_equal and was_current:
            translated["source_revision"] = result["source_revision"]
        else:
            translated["source_revision"] = result["source_revision"] - 1
            translated["reviewed_by"] = None
            translated["reviewed_at"] = None
        translated["term_id"] = target["id"]
        previous_target = next((v for o, v in candidates if o["id"] == target["id"]), None)
        if previous_target is not None and (owner["id"] != target["id"] or translated != value):
            translated["version"] = max(previous_target["version"], value["version"]) + 1
        translations.append(translated)
    result["translations"] = translations
    return result
