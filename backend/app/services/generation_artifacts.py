"""Verify prepared artifacts before switching the canonical publication."""
import codecs
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


def artifact_manifest(settings, doc_id: str, generation_id: str, *, written_hashes=None) -> dict[str, str]:
    # A writer can supply hashes of the bytes it just wrote. Publication always
    # calls this without the hint and independently reads every actual file.
    paths = generation_paths(settings, doc_id, generation_id)
    from app.services.table_quality import validate_table_quality_artifact
    validate_table_quality_artifact(paths.bundle)
    result = {}
    for name, root in (("attachments", paths.attachments), ("bundle", paths.bundle)):
        _check_tree(root)
        for path in sorted(root.rglob("*")):
            if path.is_file():
                key=f"{name}/{path.relative_to(root).as_posix()}"
                result[key] = written_hashes[key] if written_hashes and key in written_hashes else file_digest(path)
    if written_hashes and not written_hashes.keys() <= result.keys():
        raise GenerationIntegrityError()
    return result


def _read_hashed_json(path: Path, expected_hash: str | None) -> dict:
    """Decode bounded byte blocks, hashing exactly the bytes consumed once.

    JSON still needs the complete decoded text. Joining decoder pieces before
    parsing releases them, avoiding both an additional raw document and UTF-8
    decoder over-allocation for large strings containing astral characters.
    Preserve json.loads(bytes) encoding and surrogate handling, including BOMs.
    """
    if not expected_hash:
        raise GenerationIntegrityError()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        prefix = stream.read(4)
        digest.update(prefix)
        decoder = codecs.getincrementaldecoder(json.detect_encoding(prefix))(errors="surrogatepass")
        pieces = [decoder.decode(prefix)]
        while chunk := stream.read(64 * 1024):
            digest.update(chunk)
            pieces.append(decoder.decode(chunk))
        pieces.append(decoder.decode(b"", final=True))
    if digest.hexdigest() != expected_hash:
        raise GenerationIntegrityError()
    decoded = "".join(pieces)
    del pieces
    return json.loads(decoded)


def load_verified_publication(settings, doc_id: str, generation_id: str, expected_hash: str | None) -> dict:
    try:
        paths = generation_paths(settings, doc_id, generation_id)
        manifest_path = paths.uploads_root / "publication.json"
        _reject_links(manifest_path)
        prepared = _read_hashed_json(manifest_path, expected_hash)
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
