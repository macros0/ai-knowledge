"""Versioned, text-free diagnostics stored with a document generation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CauseCode = Literal['classifier_output_truncated', 'classifier_timeout', 'classifier_invalid_result',
                    'header_ambiguous', 'formula_result_missing', 'content_omitted']


class LegacyTableLocator(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['legacy'] = 'legacy'
    source_id: str = Field(min_length=1, max_length=256, pattern=r'^root(?:/[A-Za-z0-9_-]+)*$')
    chunk_index: int = Field(ge=0)
    table_ordinal: int = Field(ge=0)


class TableQualityReport(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    table_ref: LegacyTableLocator
    method: Literal['llm_per_row', 'llm_whole', 'llm_generation', 'heuristic_xml', 'deterministic_rows', 'unprocessed']
    cause_code: CauseCode | None = None
    input_rows: int | None = Field(default=None, ge=0)
    covered_rows: int | None = Field(default=None, ge=0)
    input_cells: int | None = Field(default=None, ge=0)
    covered_cells: int | None = Field(default=None, ge=0)
    header_status: Literal['unknown', 'ambiguous', 'verified'] = 'unknown'
    schema_version: Literal[1] = 1

    @model_validator(mode='after')
    def check_coverage(self):
        for before, after in [(self.input_rows, self.covered_rows), (self.input_cells, self.covered_cells)]:
            if after is not None and (before is None or after > before):
                raise ValueError('Coverage cannot exceed or invent the input count')
        return self


class TableQualityArtifact(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal[1] = 1
    reports: list[TableQualityReport]


def classifier_cause(exc: Exception) -> CauseCode:
    from app.services.llm_client import LLMTruncationError
    if isinstance(exc, LLMTruncationError):
        return 'classifier_output_truncated'
    import httpx
    import litellm
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, litellm.Timeout)):
        return 'classifier_timeout'
    return 'classifier_invalid_result'


def bind_table_reports(events: list[dict], source_id: str, chunk_index: int) -> list[dict]:
    """The pipeline supplies canonical source/chunk identity, never the LLM."""
    result = []
    for event in events:
        if event.get('event') == 'table_quality':
            report = TableQualityReport.model_validate(event['report'])
            report.table_ref = LegacyTableLocator(source_id=source_id, chunk_index=chunk_index,
                                                  table_ordinal=report.table_ref.table_ordinal)
            event = {'event': 'table_quality', 'report': report.model_dump(mode='json')}
        result.append(event)
    return result


def reports_from_chunks(chunks_data: dict) -> list[TableQualityReport]:
    return [TableQualityReport.model_validate(event['report'])
            for index, info in sorted(chunks_data.items(), key=lambda pair: int(pair[0]))
            for event in (info or {}).get('degradation', [])
            if event.get('event') == 'table_quality']


def has_content_omission(chunks_data: dict) -> bool:
    return any(report.cause_code == 'content_omitted'
               or (report.covered_rows is not None and report.input_rows is not None and report.covered_rows < report.input_rows)
               or (report.covered_cells is not None and report.input_cells is not None and report.covered_cells < report.input_cells)
               for report in reports_from_chunks(chunks_data))


def write_table_quality_report(generation_bundle: Path, reports: list[TableQualityReport]) -> Path:
    from app.services.generation_files import _reject_links
    from app.services.json_atomic import write_json_atomic
    artifact = TableQualityArtifact(reports=reports)
    path = generation_bundle / 'table-quality.json'
    _reject_links(path)
    generation_bundle.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, artifact.model_dump(mode='json'))
    return path


def validate_table_quality_artifact(bundle: Path) -> None:
    path = bundle / 'table-quality.json'
    from app.services.generation_files import _reject_links
    _reject_links(path)
    if path.is_file():
        TableQualityArtifact.model_validate(json.loads(path.read_text(encoding='utf-8')))
