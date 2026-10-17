"""Compact structural classification; model selects, Python preserves values."""
from dataclasses import dataclass, asdict
from hashlib import sha256
import json

from app.config import get_settings
from app.services.llm_scheduler import LLMCancelled
from app.services.json_atomic import write_json_atomic

from app.services.llm_profiles import TableClassificationResult
from app.services.table_projection import build_classification_preview


@dataclass(frozen=True)
class StructuralClassification:
    title_column: int
    concept_type: str
    extraction_mode: str
    cause_code: str | None = None


def classify_table(descriptor, sample_rows, limits, llm=None, *, system_prompt='', doc_id='unknown'):
    preview = build_classification_preview(descriptor, sample_rows, limits)
    fallback = StructuralClassification(preview.column_index_map[0], 'reference', 'per_row')
    if llm is None:
        return fallback
    system = system_prompt + "\nUse original one-based column indices from the JSON preview; title_col must be one of these indices."
    settings = get_settings()
    key = sha256(json.dumps({'v':'structure-1', 'schema':descriptor.schema_version,
        'normalizer':descriptor.normalizer_version, 'model':settings.llm_model, 'prompt':system,
        'preview':preview.text}, sort_keys=True).encode()).hexdigest()
    path = settings.cache_dir / 'table_classify' / ('structural-' + key + '.json')
    from app.services.field_table import _fresh_since
    since = getattr(_fresh_since, 'value', None)
    try:
        if path.is_file() and (since is None or path.stat().st_mtime >= since):
            cached = StructuralClassification(**json.loads(path.read_text(encoding='utf8')))
            if cached.title_column in preview.column_index_map and cached.extraction_mode in {'per_row','whole'} and cached.concept_type in {'concept','procedure','reference','note'} and cached.cause_code is None:
                return cached
    except (OSError, ValueError, TypeError):
        pass
    try:
        result = llm.chat_json(system, preview.text, doc_id=doc_id,
                               single_object=True, task='table_classification')
        result = TableClassificationResult.model_validate(result)
        # Structural prompt uses original, one-based Excel indices. Mapping is
        # checked without translating a hallucinated index into another column.
        if result.title_col not in preview.column_index_map:
            raise ValueError('Unrepresented title column')
        decision = StructuralClassification(result.title_col, result.concept_type, result.extraction_mode)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(path, asdict(decision))
        except OSError:
            pass  # Cache availability cannot change extraction completeness.
        return decision
    except LLMCancelled:
        raise
    except Exception as exc:
        from app.services.table_quality import classifier_cause
        return StructuralClassification(fallback.title_column, fallback.concept_type, fallback.extraction_mode,
                                        classifier_cause(exc))
