"""Offline, metadata-only audit of exact glossary identities."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.glossary.identity import (
    IdentityNamespaceConflictError, collect_identity_conflicts, validate_namespace,
)
from app.services.glossary.normalization import GlossaryValidationError
from app.services.glossary.types import InfotypeRuleSnapshot


def audit_existing_glossary(terms: tuple[dict, ...], rules=()) -> dict:
    rules = tuple(rules)
    conflicts = []
    validation_errors = []
    try:
        validate_namespace(terms, rules)
    except IdentityNamespaceConflictError as exc:
        conflicts = list(exc.conflicts)
    except GlossaryValidationError as exc:
        validation_errors.append(str(exc))
        # Preserve discoverable literal conflicts even when legacy metadata
        # fails readiness validation. Invalid rules may also stop this scan;
        # retain that reason instead of reporting a successful audit.
        try:
            conflicts = list(collect_identity_conflicts(terms, rules))
        except GlossaryValidationError as conflict_error:
            if str(conflict_error) not in validation_errors:
                validation_errors.append(str(conflict_error))
    return {"term_count": len(terms), "rule_count": len(rules),
            "valid": not conflicts and not validation_errors,
            "validation_errors": validation_errors,
            "conflict_count": len(conflicts), "conflicts": conflicts}


def load_rules(path: Path | None) -> tuple[InfotypeRuleSnapshot, ...]:
    if path is None:
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rules", payload) if isinstance(payload, dict) else payload
    return tuple(
        InfotypeRuleSnapshot(
            rule_id=int(item.get("id", index + 1)),
            name=str(item["name"]),
            number_from=int(item["number_from"]),
            number_to=int(item["number_to"]),
            prefixes=tuple(item.get("prefixes") or ()),
            enabled=bool(item.get("enabled", True)),
            version=int(item.get("version", 1)),
        )
        for index, item in enumerate(rows)
    )


def namespace_from_session(session) -> tuple[tuple[dict, ...], tuple[InfotypeRuleSnapshot, ...]]:
    """Detach all terms and rules, including disabled records, in this session."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.db.models import DomainTerm, GlossaryInfotypeRule
    from app.services.glossary.registry import _term_dict

    terms = tuple(_term_dict(term, {}) for term in session.scalars(
        select(DomainTerm).options(selectinload(DomainTerm.aliases_rel),
                                  selectinload(DomainTerm.translations_rel)).order_by(DomainTerm.id)
    ))
    rules = tuple(InfotypeRuleSnapshot(
        rule_id=rule.id, name=rule.name, number_from=rule.number_from, number_to=rule.number_to,
        prefixes=tuple(prefix.prefix for prefix in sorted(rule.prefixes_rel, key=lambda item: item.position)),
        enabled=rule.enabled, version=rule.version,
    ) for rule in session.scalars(select(GlossaryInfotypeRule).options(
        selectinload(GlossaryInfotypeRule.prefixes_rel)).order_by(GlossaryInfotypeRule.id)))
    return terms, rules


def load_existing_namespace() -> tuple[tuple[dict, ...], tuple[InfotypeRuleSnapshot, ...]]:
    """Read one coherent revision without acquiring a glossary write session."""
    from sqlalchemy import select

    from app.db.models import GlossaryState
    from app.db.session import session_scope

    for _attempt in range(3):
        with session_scope() as session:
            revision_stmt = select(GlossaryState.revision).where(GlossaryState.id == 1)
            revision = session.scalar(revision_stmt)
            namespace = namespace_from_session(session)
            if session.scalar(revision_stmt) == revision:
                return namespace
    raise RuntimeError("Глоссарий изменился во время чтения аудита; повторите аудит")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    terms, rules = load_existing_namespace()
    if args.rules_file is not None:
        rules = load_rules(args.rules_file)
    result = audit_existing_glossary(terms, rules)
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
