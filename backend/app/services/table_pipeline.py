"""Glue between structural parsing, candidate tables and existing generation jobs."""
from dataclasses import replace
from itertools import islice
from pathlib import Path
import shutil

from sqlalchemy import select

from app.db.models import DocumentTable, DocumentTableRow
from app.db.session import session_scope
from app.services.generation_store import lock_writable_generation
from app.services.table_classification import classify_table
from app.services.table_projection import BudgetEstimator, ProjectionLimits, project_table
from app.services.table_store import (descriptor_from_dict, _stored_rows, _row, export_generation_tables,
                                      import_generation_tables, verify_generation_tables)
from app.services.table_quality import StructuralTableLocator, TableQualityReport


def reset_candidate_tables(doc_id, generation_id):
    with session_scope() as session:
        lock_writable_generation(session, doc_id, generation_id)
        session.query(DocumentTableRow).filter_by(doc_id=doc_id, generation_id=generation_id).delete(synchronize_session=False)
        session.query(DocumentTable).filter_by(doc_id=doc_id, generation_id=generation_id).delete(synchronize_session=False)


def import_parser_spool(doc_id, generation_id, folder):
    # Fixed private name; never derived from table/source IDs supplied by a file.
    portable = folder.parent / '.tables-import'
    portable.mkdir(exist_ok=False)
    shutil.move(str(folder), str(portable / 'tables'))
    try:
        import_generation_tables(doc_id, generation_id, portable)
    finally:
        shutil.rmtree(portable)


def build_table_source_chunks(doc_id, generation_id, settings, generator):
    result = []
    limits = ProjectionLimits(classification_tokens=settings.table_classification_input_tokens,
        embedding_tokens=settings.table_embedding_tokens, context_tokens=settings.table_context_tokens,
        max_fragment_chars=min(settings.okf_max_concept_chars, settings.okf_max_chunk_index_chars,
                               settings.okf_max_chunk_chars, 2000) - 256)
    with session_scope() as session:
        verify_generation_tables(session, doc_id, generation_id)
        for table in session.scalars(select(DocumentTable).where(DocumentTable.doc_id == doc_id,
                                    DocumentTable.generation_id == generation_id).order_by(DocumentTable.table_id)):
            descriptor = descriptor_from_dict(table.descriptor)
            sample = list(islice((_row(r) for r in _stored_rows(session, doc_id, generation_id, table.table_id)), 8))
            decision = classify_table(descriptor, sample, limits,
                generator.llm if settings.okf_table_llm_classify else None,
                system_prompt=generator.prompts.get('okf_table_classifier_structural'), doc_id=doc_id)
            quality = dict(table.quality)
            quality['classifier_cause'] = decision.cause_code
            quality['extraction_mode'] = decision.extraction_mode
            table.quality = quality
            # Dense inputs add the title to content. Reserve its worst-case
            # UTF-8 budget before splitting, including non-ASCII sheet names.
            title_template = f'{descriptor.sheet_name[:64]} — {descriptor.cell_range.max_row} [ffffffff]'
            title_bytes = BudgetEstimator().estimate(title_template + '\n').tokens
            if title_bytes >= min(limits.embedding_tokens,limits.context_tokens):
                raise ValueError('Projection budget cannot fit the table title')
            fragment_limits = replace(limits,embedding_tokens=limits.embedding_tokens-title_bytes,
                                      context_tokens=limits.context_tokens-title_bytes)
            fragments = project_table(descriptor, (_row(r) for r in _stored_rows(session, doc_id, generation_id, table.table_id)),
                                            fragment_limits, generation_id=generation_id, title_column=decision.title_column, extraction_mode=decision.extraction_mode)
            for fragment in fragments:
                title = f'{descriptor.sheet_name[:64]} — {fragment.table_ref.row_number} [{fragment.fragment_id[:8]}]'
                result.append({'source_id': table.source_id, 'content': fragment.content, 'fragment_id': fragment.fragment_id,
                               'table_title': title, 'concept_type': decision.concept_type,
                               'table_refs': [r.model_dump(mode='json') for r in fragment.coverage_refs]})
    return result


def prepare_table_artifacts(doc_id, generation_id, bundle: Path):
    reports = []
    with session_scope() as session:
        tables = list(session.scalars(select(DocumentTable).where(DocumentTable.doc_id == doc_id,
                                       DocumentTable.generation_id == generation_id)))
        if not tables:
            return reports
        export_generation_tables(session, doc_id, generation_id, bundle)
        for table in tables:
            q = table.quality
            # Counts reflect independently verified OOXML cells, including headers.
            cause = q.get('classifier_cause')
            if table.descriptor['method'] == 'unresolved':
                cause = 'header_ambiguous'
            reports.append(TableQualityReport(table_ref=StructuralTableLocator(generation_id=generation_id,
                source_id=table.source_id, table_id=table.table_id), method='structural_rows', cause_code=cause,
                input_rows=q['row_count'], covered_rows=q['row_count'], input_cells=q['cell_count'],
                covered_cells=q['cell_count'], header_status=q['header_status']))
    return reports
