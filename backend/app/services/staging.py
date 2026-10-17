"""Staging-хранилище инкрементальной генерации OKF (checkpointing/resume).

Состояние задачи (manifest) хранится в реляционной БД (document_staging), сырые
файлы чанков — в FS под data/staging/{doc_id}/:
  chunk_XX.json — нормализованные концепты чанка (JSON-массив dict)
  chunk_XX.md   — сырой текст чанка (LLM-вход)

Результаты каждого чанка пишутся сразу после генерации LLM, поэтому при падении
обработанные чанки сохраняются, а повторный запуск пропускает их (has_chunk /
processed_chunks). При финализации концепты читаются из чанков в порядке
индексов, бандл собирается в okf_bundles. При неполной генерации staging
сохраняется для догенерации только проблемных чанков; при полной — удаляется.

Замена data/staging/{doc_id}/manifest.json → таблица document_staging (JSONB),
как в MIGRATION_PLAN.md §3.3/§5.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update, func, cast, literal, Text
from sqlalchemy.dialects.postgresql import JSONB, ARRAY

from app.config import get_settings
from app.db.models import DocumentStaging
from app.db.session import session_scope
from app.models.schemas import Concept
from app.services.json_atomic import write_json_atomic
from app.services.gen_quality import partial_chunk_indices
from app.services.okf_generator import _slugify


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Лок на append_chunk общий для всех экземпляров StagingStore одного doc_id:
# per-instance threading.Lock() не защищал бы при двух потоках пайплайна на один
# staging (у каждого экземпляра был бы свой лок). Ключ — doc_id.
_STAGING_LOCKS: dict[str, threading.Lock] = {}
_STAGING_LOCKS_GUARD = threading.Lock()


def _staging_lock(doc_id: str) -> threading.Lock:
    with _STAGING_LOCKS_GUARD:
        lock = _STAGING_LOCKS.get(doc_id)
        if lock is None:
            lock = threading.Lock()
            _STAGING_LOCKS[doc_id] = lock
        return lock


class StagingStore:
    def __init__(self, doc_id: str, staging_root: Path | None = None):
        self.doc_id = doc_id
        self.dir = staging_root or get_settings().staging_dir / doc_id
        self._lock = _staging_lock(doc_id)

    def exists(self) -> bool:
        return self._load_raw() is not None

    def create(
        self,
        total_chunks: int,
        global_tags: list[str] | None = None,
        parser_version: str | None = None,
        source_file_hash: str | None = None,
        generation_id: str | None = None,
        processing_scope_hash: str | None = None,
    ) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "task_id": self.doc_id,
            "status": "in_progress",
            "total_chunks": total_chunks,
            "last_updated": _now(),
            "processed_chunks": [],
            "used_slugs": [],
            "global_tags": global_tags or [],
            "chunks_data": {},
            "parser_version": parser_version,
            "source_file_hash": source_file_hash,
            "generation_id": generation_id,
            "processing_scope_hash": processing_scope_hash,
        }
        self._write(manifest)
        return manifest

    def _load_raw(self) -> dict | None:
        with session_scope() as s:
            row = s.get(DocumentStaging, self.doc_id)
            if row is None:
                return None
            return {
                "task_id": row.doc_id,
                "status": row.status,
                "total_chunks": row.total_chunks,
                "last_updated": row.updated_at.isoformat() if row.updated_at else None,
                "processed_chunks": list(row.processed_chunks or []),
                "used_slugs": list(row.used_slugs or []),
                "global_tags": list(row.global_tags or []),
                "chunks_data": dict(row.chunks_data or {}),
                "parser_version": row.parser_version,
                "source_file_hash": row.source_file_hash,
                "generation_id": row.generation_id,
                "processing_scope_hash": row.processing_scope_hash,
            }

    def _reference_bytes(self, filename: str) -> bytes:
        match = re.fullmatch(r"table_refs_([a-f0-9]{64})\.json", filename)
        if match is None:
            raise ValueError("Invalid staging table reference filename")
        path = self.dir / filename
        if path.is_symlink():
            raise ValueError("Invalid staging table reference file")
        with path.open("rb") as handle:
            payload = handle.read(4 * 1024 * 1024 + 1)
        if len(payload) > 4 * 1024 * 1024:
            raise ValueError("Staging table reference file exceeds size limit")
        if sha256(payload).hexdigest() != match.group(1):
            raise ValueError("Staging table reference checksum mismatch")
        return payload

    def _pack_info(self, info: dict) -> dict:
        result = dict(info)
        if "table_refs" not in result:
            return result
        result.pop("table_refs_file", None)
        if not result["table_refs"]:
            return result
        payload = json.dumps(result["table_refs"], ensure_ascii=False, indent=None).encode("utf8")
        if len(payload) > 4 * 1024 * 1024:
            raise ValueError("Staging table reference file exceeds size limit")
        filename = f"table_refs_{sha256(payload).hexdigest()}.json"
        path = self.dir / filename
        # Files are immutable and content addressed: a failed DB commit cannot
        # alter the references used by the previous durable checkpoint.
        if path.exists():
            self._reference_bytes(filename)
        else:
            write_json_atomic(path, result["table_refs"], indent=None)
        result.pop("table_refs")
        result["table_refs_file"] = filename
        return result

    def _unpack_info(self, info: dict) -> dict:
        result = dict(info)
        filename = result.pop("table_refs_file", None)
        if filename is not None:
            if "table_refs" in result:
                raise ValueError("Conflicting staging table reference representations")
            refs = json.loads(self._reference_bytes(filename))
            if not isinstance(refs, list):
                raise ValueError("Invalid staging table references")
            result["table_refs"] = refs
        return result

    def load(self) -> dict | None:
        manifest = self._load_raw()
        if manifest is not None:
            manifest["chunks_data"] = {index: self._unpack_info(info)
                                        for index, info in manifest["chunks_data"].items()}
        return manifest

    def _write(self, manifest: dict) -> None:
        packed = {index: self._pack_info(info) for index, info in manifest.get("chunks_data", {}).items()}
        with session_scope() as s:
            row = s.get(DocumentStaging, self.doc_id)
            if row is None:
                row = DocumentStaging(doc_id=self.doc_id)
                s.add(row)
            row.total_chunks = manifest.get("total_chunks", 0)
            row.processed_chunks = list(manifest.get("processed_chunks", []))
            row.used_slugs = list(manifest.get("used_slugs", []))
            row.global_tags = list(manifest.get("global_tags", []))
            row.chunks_data = packed
            row.parser_version = manifest.get("parser_version")
            row.source_file_hash = manifest.get("source_file_hash")
            row.generation_id = manifest.get("generation_id")
            row.processing_scope_hash = manifest.get("processing_scope_hash")
            row.status = manifest.get("status", "in_progress")

    @property
    def generation_id(self) -> str | None:
        """Read the attempt pointer without hydrating table coverage metadata."""
        with session_scope() as session:
            return session.scalar(select(DocumentStaging.generation_id).where(
                DocumentStaging.doc_id == self.doc_id))

    @property
    def processed_chunks(self) -> list[int]:
        # The per-chunk resume check must not hydrate all table coverage refs.
        with session_scope() as session:
            processed = session.scalar(select(DocumentStaging.processed_chunks).where(
                DocumentStaging.doc_id == self.doc_id))
        return sorted(processed or [])

    def _load_checkpoint(self, index: int) -> dict | None:
        with session_scope() as session:
            row = session.execute(select(DocumentStaging.processed_chunks,
                DocumentStaging.used_slugs, DocumentStaging.global_tags,
                DocumentStaging.chunks_data[str(index)].label('chunk_info')).where(
                DocumentStaging.doc_id == self.doc_id)).mappings().one_or_none()
        if row is None:
            return None
        return {'processed_chunks': row['processed_chunks'] or [],
                'used_slugs': row['used_slugs'] or [], 'global_tags': row['global_tags'] or [],
                'chunks_data': {str(index): self._unpack_info(row['chunk_info'] or {})}}

    def _write_checkpoint(self, index: int, manifest: dict) -> None:
        # Patch only this entry inside SQL. Returning the whole coverage map to
        # Python on every fragment made wide-table checkpointing quadratic.
        info = json.dumps(self._pack_info(manifest['chunks_data'][str(index)]), ensure_ascii=False)
        with session_scope() as session:
            if session.get_bind().dialect.name == 'postgresql':
                base = func.coalesce(func.nullif(cast(DocumentStaging.chunks_data, JSONB),
                    cast(literal('null'), JSONB)), cast(literal('{}'), JSONB))
                patched = func.jsonb_set(base, cast([str(index)], ARRAY(Text)),
                                         cast(literal(info), JSONB), True)
            else:
                base = func.coalesce(func.nullif(DocumentStaging.chunks_data, 'null'), '{}')
                patched = func.json_set(base, f'$."{index}"', func.json(info))
            session.execute(update(DocumentStaging).where(DocumentStaging.doc_id == self.doc_id).values(
                processed_chunks=manifest['processed_chunks'], used_slugs=manifest['used_slugs'],
                chunks_data=patched, status='in_progress'))

    def set_chunks_metadata(self, entries) -> None:
        """Checkpoint source metadata in bounded batches; preserve completed work.

        An interrupted input leaves earlier batches durable. Preparation can
        safely replay those entries before resume continues generation.
        """
        from itertools import islice
        source = iter(entries)
        while batch := list(islice(source, 256)):
            with self._lock:
                manifest = self._load_raw()
                if manifest is None:
                    raise ValueError('Cannot prepare chunk metadata without staging')
                for entry in batch:
                    index = str(entry['index'])
                    info = dict(manifest['chunks_data'].get(index) or {})
                    info['source_id'] = entry['source_id']
                    if entry.get('table_refs'):
                        info['table_refs'] = entry['table_refs']
                        info['table_title'] = entry.get('table_title', '')
                    manifest['chunks_data'][index] = info
                self._write(manifest)

    def bind_generation(self, generation_id: str) -> None:
        with self._lock:
            manifest = self._load_raw()
            if manifest is None:
                raise ValueError("Cannot bind a generation without staging")
            manifest["generation_id"] = generation_id
            self._write(manifest)

    def has_chunk(self, index: int) -> bool:
        return index in self.processed_chunks

    @property
    def partial_chunks(self) -> list[int]:
        return partial_chunk_indices((self._load_raw() or {}).get("chunks_data", {}))

    def save_chunk_text(self, index: int, text: str) -> None:
        """Сохраняет сырой текст чанка (LLM-вход) для просмотра в UI.

        Файлы chunk_XX.md (текст) не конфликтуют с chunk_XX.json (концепты).
        Идемпотентно: повторная запись перезаписывает тот же текст.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        # Checkpoints are platform-neutral Markdown. Without an explicit
        # newline policy Windows expands an already-CRLF source to CRCRLF.
        with (self.dir / f"chunk_{index:02d}.md").open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def set_chunk_source(self, index: int, source_id: str) -> None:
        """Сохраняет происхождение чанка до первого LLM-вызова."""
        with self._lock:
            manifest = self._load_raw() or self.create(index + 1, global_tags=[])
            chunks_data = dict(manifest.get("chunks_data", {}))
            info = dict(chunks_data.get(str(index)) or {})
            info["source_id"] = source_id
            chunks_data[str(index)] = info
            manifest["chunks_data"] = chunks_data
            self._write(manifest)

    def set_chunk_table_refs(self, index: int, refs: list[dict], *, title: str = "") -> None:
        with self._lock:
            manifest = self._load_raw()
            info = dict(manifest["chunks_data"].get(str(index)) or {})
            info["table_refs"] = refs
            info["table_title"] = title
            manifest["chunks_data"][str(index)] = info
            self._write(manifest)

    def append_chunk(
        self,
        index: int,
        concepts: list[Concept],
        degradation: list[dict] | None = None,
        provenance: dict | None = None,
        global_tags: list[str] | None = None,
    ) -> list[str]:
        """Сохраняет концепты чанка и обновляет manifest. Возвращает занятые слаги.

        degradation — события деградации генерации этого чанка (телеметрия
        gen_quality: salvage JSON, fallback классификатора таблиц). Хранятся
        в chunks_data, при финализации агрегируются в documents.problem.

        provenance — провенанс генерации этого чанка (Этап 2b):
        {generated_at (ISO), model_id, prompt_version}. Переживает resume:
        при финализации попадает в okf_concepts.generated_at/model_id/
        prompt_version, не перезаписываясь при смене только тегов/разработки.

        global_tags — пользовательские теги на момент этой генерации, чтобы
        последующая догенерация не возвращала удалённые пользователем теги.
        """
        with self._lock:
            manifest = self._load_checkpoint(index) or self.create(index + 1, global_tags=[])
            processed = list(manifest.get("processed_chunks", []))
            if index not in processed:
                processed.append(index)
                processed.sort()
            chunks_data = dict(manifest.get("chunks_data", {}))
            previous_slugs = set((chunks_data.get(str(index)) or {}).get("slugs", []))
            used = [slug for slug in manifest.get("used_slugs", []) if slug not in previous_slugs]
            slugs = self._allocate_slugs(concepts, used)
            used.extend(slugs)
            chunk_file = f"chunk_{index:02d}.json"
            write_json_atomic(self.dir / chunk_file, [c.model_dump() for c in concepts])
            previous_info = chunks_data.get(str(index)) or {}
            info = {k: previous_info[k] for k in ("table_refs", "table_title") if k in previous_info}
            if previous_info.get("source_id"):
                info["source_id"] = previous_info["source_id"]
            info.update({"file": chunk_file, "concepts_count": len(concepts), "slugs": slugs})
            if degradation:
                info["degradation"] = degradation
            if provenance:
                info["provenance"] = provenance
            info["global_tags"] = list(global_tags if global_tags is not None else manifest.get("global_tags", []))
            chunks_data[str(index)] = info
            manifest.update(
                {
                    "processed_chunks": processed,
                    "used_slugs": used,
                    "chunks_data": chunks_data,
                    "status": "in_progress",
                }
            )
            self._write_checkpoint(index, manifest)
            return slugs

    def concepts(self) -> list[Concept]:
        manifest = self._load_raw()
        if not manifest:
            return []
        result: list[Concept] = []
        for index in sorted(int(k) for k in manifest.get("chunks_data", {})):
            info = manifest["chunks_data"][str(index)]
            if not info.get("file"):
                continue
            path = self.dir / info["file"]
            if not path.is_file():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for item in raw:
                if isinstance(item, dict):
                    try:
                        result.append(Concept(**item))
                    except Exception:
                        continue
        return result

    def slugs(self) -> list[str]:
        manifest = self._load_raw() or {}
        slugs: list[str] = []
        for index in sorted(int(k) for k in manifest.get("chunks_data", {})):
            info = manifest["chunks_data"][str(index)]
            if info.get("file"):
                slugs.extend(info.get("slugs", []))
        return slugs

    def remove(self) -> None:
        with session_scope() as s:
            row = s.get(DocumentStaging, self.doc_id)
            if row is not None:
                s.delete(row)
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)

    @staticmethod
    def _allocate_slugs(concepts: list[Concept], used: list[str]) -> list[str]:
        """Дедупликация слагов в процессе: коллизии получают суффикс -1, -2, ..."""
        used_set = set(used)
        slugs: list[str] = []
        for concept in concepts:
            base = _slugify(concept.title)
            slug = base
            n = 1
            while slug in used_set:
                slug = f"{base}-{n}"
                n += 1
            used_set.add(slug)
            slugs.append(slug)
        return slugs
