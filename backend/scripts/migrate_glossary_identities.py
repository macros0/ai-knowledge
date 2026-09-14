"""Simulate and apply an approved identity plan without Qdrant or LLM."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def plan_checksum(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _metadata_json(value) -> str:
    from scripts.glossary_migration_metadata import json_value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=json_value)


def _result_checksum(result: dict) -> str:
    # The portable writer lock performs an UPDATE that refreshes this timestamp
    # even for a replay. Revision/readiness still participate in the checksum.
    value = {**result, "state": {key: item for key, item in (result.get("state") or {}).items()
                                if key != "updated_at"}}
    return hashlib.sha256(_metadata_json(value).encode()).hexdigest()


def _set_ready(ready: bool, *, expected_revision: int | None = None) -> dict:
    from app.services.glossary.mutation import bump_glossary_revision, glossary_write_session
    with glossary_write_session(allow_unready=True) as (session, state):
        if expected_revision is not None and state.revision != expected_revision:
            raise ValueError(f"Ревизия глоссария устарела: ожидалась {expected_revision}, фактически {state.revision}")
        state.identities_ready = ready
        bump_glossary_revision(session, state)
        return {"status": "ready" if ready else "prepared", "glossary_revision": state.revision}


def _resolutions(plan):
    return plan.get("resolutions") or plan.get("merges") or []


def _merge_request(resolution):
    request = dict(resolution)
    request["source_term_id"] = resolution.get("source_term_id", resolution.get("source"))
    request["target_term_id"] = resolution.get("target_term_id", resolution.get("target"))
    if not all(type(request[field]) is int for field in ("source_term_id", "target_term_id")) or not request.get("request_id"):
        raise ValueError("Для применения нужны source, target и request_id")
    return request


def _check_initial_versions(session, state, plan):
    from sqlalchemy import select
    from app.db.models import DomainTerm
    expected_revision = plan.get("expected_revision")
    if expected_revision is not None and state.revision != expected_revision:
        raise ValueError(f"Ревизия глоссария устарела: ожидалась {expected_revision}, фактически {state.revision}")
    versions = dict(session.execute(select(DomainTerm.id, DomainTerm.version)).all())

    def check(term_id, version):
        if term_id not in versions:
            raise ValueError(f"Термин не найден: {term_id}")
        if version is not None and versions[term_id] != version:
            raise ValueError(f"Версия термина устарела: {term_id}")

    for action in [*(plan.get("term_updates") or []), *(plan.get("alias_deletions") or [])]:
        check(action.get("term_id"), action.get("version"))
    request_ids = set()
    for resolution in _resolutions(plan):
        request = _merge_request(resolution)
        if request["request_id"] in request_ids:
            raise ValueError("request_id повторяется внутри плана")
        request_ids.add(request["request_id"])
        check(request["source_term_id"], request.get("source_version"))
        check(request["target_term_id"], request.get("target_version"))
        if request.get("expected_revision") is not None and request["expected_revision"] != state.revision:
            raise ValueError("Ревизия merge устарела")
    return versions


def _apply_plan(session, state, plan, registry):
    """Shared implementation: caller supplies either private or locked session."""
    from sqlalchemy import delete
    from app.db.models import DomainTerm, DomainTermAlias, GlossaryIdentityKey, GlossaryInfotypePrefix, GlossaryInfotypeRule
    from app.services.glossary.identity import validate_namespace
    from app.services.glossary.normalization import validate_infotype_number
    from app.services.glossary.registry import _rebuild_all_identity_keys
    from app.services.glossary.rules import validate_rule
    from scripts.audit_glossary_identities import namespace_from_session

    versions = _check_initial_versions(session, state, plan)
    touched = set()
    updates = {}
    for update in plan.get("term_updates") or []:
        term_id = update["term_id"]
        if "infotype_number" not in update:
            raise ValueError("term_updates требует явное infotype_number")
        number = update["infotype_number"]
        if term_id in updates and updates[term_id] != number:
            raise ValueError(f"Противоречивые обновления термина: {term_id}")
        updates[term_id] = number
    for term_id, number in updates.items():
        term = session.get(DomainTerm, term_id)
        validate_infotype_number(term.kind, number)
        term.infotype_number = number
        touched.add(term_id)
    deleted_aliases = set()
    for deletion in plan.get("alias_deletions") or []:
        alias_id = deletion.get("alias_id")
        alias = session.get(DomainTermAlias, alias_id)
        if alias_id in deleted_aliases or alias is None or alias.term_id != deletion["term_id"]:
            raise ValueError(f"Алиас для удаления не найден или повторяется: {alias_id}")
        deleted_aliases.add(alias_id)
        touched.add(alias.term_id)
        session.delete(alias)
    for rule_data in plan.get("rules") or []:
        name = str(rule_data["name"]).strip()
        number_from, number_to = int(rule_data["number_from"]), int(rule_data["number_to"])
        prefixes = validate_rule(number_from, number_to, tuple(rule_data.get("prefixes") or ()), name=name)
        rule = GlossaryInfotypeRule(name=name, number_from=number_from, number_to=number_to,
                                   enabled=bool(rule_data.get("enabled", True)), created_by="migration", updated_by="migration")
        rule.prefixes_rel = [GlossaryInfotypePrefix(prefix=prefix, normalized_prefix=prefix, position=index)
                             for index, prefix in enumerate(prefixes)]
        session.add(rule)
    session.flush()
    session.expire_all()
    applied = []
    for resolution in _resolutions(plan):
        request = _merge_request(resolution)
        source_id, target_id = request["source_term_id"], request["target_term_id"]
        source, target = session.get(DomainTerm, source_id), session.get(DomainTerm, target_id)
        if source is None or target is None:
            raise ValueError(f"Термин для merge не найден: {source_id}->{target_id}")
        # File versions were checked against the initial snapshot above. Internal
        # CAS now follows this transaction's intermediate merge versions.
        request.update(source_version=source.version, target_version=target.version, expected_revision=state.revision)
        digest = registry.merge_preview(request, _session=session, _state=state)["digest"]
        registry.merge(request, digest, actor_id=resolution.get("actor_id", "migration"), _session=session, _state=state,
                       _defer_identity_validation=not state.identities_ready)
        touched.update((source_id, target_id))
        applied.append({"source": source_id, "target": target_id, "request_id": request["request_id"]})
    for term_id in touched:
        term = session.get(DomainTerm, term_id)
        if term is not None:
            term.version = versions[term_id] + 1
            term.updated_by = "migration"
    session.flush()
    session.expire_all()
    validate_namespace(*namespace_from_session(session))
    session.execute(delete(GlossaryIdentityKey))
    _rebuild_all_identity_keys(session)
    state.identities_ready = True
    state.revision += 1
    session.flush()
    return applied


def _simulate(rows, plan, registry):
    from app.db.models import GlossaryState
    from scripts.glossary_migration_metadata import public_metadata, read_metadata, simulation_session
    with simulation_session(rows) as session:
        state = session.get(GlossaryState, 1)
        if state is None:
            raise ValueError("Состояние глоссария не инициализировано")
        applied = _apply_plan(session, state, plan, registry)
        return applied, public_metadata(read_metadata(session))


def _previous_result(session, checksum, current):
    from sqlalchemy import select
    from app.db.models import AuditLog
    from app.services import audit
    record = session.scalar(select(AuditLog).where(
        AuditLog.action_type == audit.GLOSSARY_IDENTITY_MIGRATION,
        AuditLog.target_id == checksum).order_by(AuditLog.id.desc()).limit(1))
    if record is None:
        return None
    if (record.meta or {}).get("result_checksum") != _result_checksum(current):
        raise ValueError("План миграции устарел: итоговые метаданные изменены после применения")
    return {"status": "unchanged", "checksum": checksum,
            "result_checksum": record.meta["result_checksum"], "applied": 0,
            "conflicts": 0, "merges": [], "result": current}


def _load_readonly_metadata():
    from sqlalchemy import select
    from app.db.models import GlossaryState
    from app.db.session import session_scope
    from scripts.glossary_migration_metadata import read_metadata
    for _attempt in range(3):
        with session_scope() as session:
            revision_stmt = select(GlossaryState.revision).where(GlossaryState.id == 1)
            revision = session.scalar(revision_stmt)
            rows = read_metadata(session)
            if session.scalar(revision_stmt) == revision:
                return rows
    raise ValueError("Глоссарий изменился во время чтения; повторите dry-run")


def apply_identity_migration(plan: dict, *, dry_run: bool = False, execute: bool = False,
                             registry=None, backup_path: Path | None = None) -> dict:
    """Validate on a private metadata copy, then atomically apply and audit.

    The legacy no-mode call only describes a plan. CLI defaults to a real
    dry-run, which reads application metadata but writes solely to private RAM.
    """
    conflicts = plan.get("conflicts") or []
    if conflicts and not any(plan.get(key) for key in ("resolutions", "merges", "term_updates", "alias_deletions", "rules")):
        raise ValueError("Каждый конфликт должен иметь явное разрешение")
    if execute and dry_run:
        raise ValueError("Нельзя одновременно указать dry_run и execute")
    checksum = plan_checksum(plan)
    if not execute and not dry_run:
        return {"status": "planned", "checksum": checksum, "conflicts": len(conflicts),
                "applied": len(_resolutions(plan)), "merges": []}
    from app.services.glossary.registry import GlossaryRegistry
    from scripts.glossary_migration_metadata import public_metadata, read_metadata
    provided_registry = registry is not None
    registry = registry or GlossaryRegistry()
    if dry_run:
        applied, result = _simulate(_load_readonly_metadata(), plan, registry)
        return {"status": "dry_run", "checksum": checksum, "result_checksum": _result_checksum(result),
                "conflicts": len(conflicts), "applied": 0, "merges": applied, "result": result}
    from app.services import audit
    from app.services.glossary.mutation import glossary_write_session
    with glossary_write_session(allow_unready=True) as (session, state):
        rows = read_metadata(session)
        before = public_metadata(rows)
        previous = _previous_result(session, checksum, before)
        if previous is not None:
            return previous
        if not provided_registry and state.identities_ready and before["terms"]:
            raise ValueError("Сначала переведите существующий глоссарий в prepared/identities_ready=false")
        _simulate(rows, plan, registry)
        if backup_path is not None:
            backup = {**before, "created_at": datetime.now(timezone.utc), "glossary_revision": state.revision}
            backup_path.write_text(_metadata_json(backup) + "\n", encoding="utf-8")
        applied = _apply_plan(session, state, plan, registry)
        result = public_metadata(read_metadata(session))
        result_checksum = _result_checksum(result)
        audit.record_in_session(session, action_type=audit.GLOSSARY_IDENTITY_MIGRATION,
                                user_id="migration", username="migration", target_type="glossary", target_id=checksum,
                                old_value=json.loads(_metadata_json(before)), new_value=json.loads(_metadata_json(result)),
                                meta={"plan_checksum": checksum, "result_checksum": result_checksum})
        return {"status": "applied", "checksum": checksum, "result_checksum": result_checksum,
                "conflicts": len(conflicts), "applied": len(applied), "merges": applied, "result": result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-file", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--prepare", action="store_true", help="lock existing glossary for metadata migration")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backup-file", type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan_file.read_text(encoding="utf-8"))
    if args.prepare:
        result = _set_ready(False, expected_revision=plan.get("expected_revision"))
    else:
        result = apply_identity_migration(plan, dry_run=not args.apply, execute=args.apply, backup_path=args.backup_file)
    print(_metadata_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
