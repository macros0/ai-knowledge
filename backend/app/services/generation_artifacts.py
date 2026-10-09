"""Verify prepared artifacts before switching the canonical publication."""
import hashlib
import json
from pathlib import Path

from app import error_codes as codes
from app.services.errors import DomainError
from app.services.generation_files import _check_tree, _reject_links, generation_paths


class GenerationIntegrityError(DomainError):
    def __init__(self):
        super().__init__(
            "Контрольные данные подготовленной версии отсутствуют или изменились. Запустите полную перегенерацию.",
            code=codes.PARTIAL_REGENERATION_UNAVAILABLE,
        )


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def artifact_manifest(settings, doc_id: str, generation_id: str) -> dict[str, str]:
    paths = generation_paths(settings, doc_id, generation_id)
    from app.services.table_quality import validate_table_quality_artifact
    validate_table_quality_artifact(paths.bundle)
    result = {}
    for name, root in (("attachments", paths.attachments), ("bundle", paths.bundle)):
        _check_tree(root)
        for path in sorted(root.rglob("*")):
            if path.is_file():
                result[f"{name}/{path.relative_to(root).as_posix()}"] = file_digest(path)
    return result


def load_verified_publication(settings, doc_id: str, generation_id: str, expected_hash: str | None) -> dict:
    try:
        paths = generation_paths(settings, doc_id, generation_id)
        manifest_path = paths.uploads_root / "publication.json"
        _reject_links(manifest_path)
        raw = manifest_path.read_bytes()
        if not expected_hash or hashlib.sha256(raw).hexdigest() != expected_hash:
            raise GenerationIntegrityError()
        prepared = json.loads(raw)
        if prepared.get("doc_id") != doc_id or prepared.get("generation_id") != generation_id:
            raise GenerationIntegrityError()
        actual = artifact_manifest(settings, doc_id, generation_id)
        if prepared.get("artifacts") != actual:
            raise GenerationIntegrityError()
        prefix = f"generations/{generation_id}/"
        for item in [*(prepared.get("attachments") or []), *(prepared.get("sources") or [])]:
            saved = item.get("saved_path")
            if saved and (not saved.startswith(prefix) or saved[len(prefix):] not in actual):
                raise GenerationIntegrityError()
        return prepared
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        if isinstance(exc, GenerationIntegrityError):
            raise
        raise GenerationIntegrityError() from exc
