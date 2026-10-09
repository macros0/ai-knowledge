"""Read-only local table acceptance probe. Detailed evidence stays outside Git.

Manifest v1: documents [{path, sha256, source_id, member?, expected_cells?,
expected_headers?}], cases [{id, category, query, evidence [{doc_id,
source_id, chunk_index?, slug?, value, header?}]}]. Paths resolve at manifest.
No answers, regeneration, index writes or formula execution are performed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _cell_key(cell):
    return cell.get('document_id', cell.get('doc_id', 'legacy')), cell['source_id'], cell['sheet'], cell['address']


def compare_reports(baseline, after):
    gates = {}
    for name, field in [('cell_coverage', 'cells'), ('header_coverage', 'headers')]:
        before, current = baseline.get(field), after.get(field)
        if before is None or current is None or not before:
            gates[name] = {'passed': False, 'status': 'unknown'}
            continue
        if field == 'cells':
            old = {_cell_key(c): _encoded(c['value']) for c in before}
            new = {_cell_key(c): _encoded(c['value']) for c in current}
            missing = len(old.keys() - new.keys())
            changed = sum(new[k] != v for k, v in old.items() if k in new)
        else:
            missing = sum((Counter(map(_encoded, before)) - Counter(map(_encoded, current))).values())
            changed = 0
        gates[name] = {'passed': not (missing or changed), 'missing': missing, 'changed': changed}
    if any(h.get('status') == 'legacy_unverified' for h in baseline.get('headers', [])):
        gates['header_correctness'] = {'passed': False, 'status': 'unknown'}
    if 'cases' in baseline or 'cases' in after:
        old = {c['id']: c for c in baseline.get('cases', [])}
        new = {c['id']: c for c in after.get('cases', [])}
        failures = [key for key in old if key not in new or not new[key].get('passed')]
        gates['retrieval_evidence'] = {'passed': bool(old) and not failures, 'failed_cases': failures}
    no_regression = all(g['passed'] for name, g in gates.items() if name != 'header_correctness')
    # Equality between two degraded snapshots is not compliance with the oracle.
    if 'gates' in baseline or 'gates' in after or 'passed' in baseline or 'passed' in after:
        gates['source_acceptance'] = {
            'passed': baseline.get('passed') is True and after.get('passed') is True,
            'baseline': baseline.get('gates', {}), 'after': after.get('gates', {}),
        }
    return {'schema_version': 1, 'passed': all(g['passed'] for g in gates.values()),
            'no_regression': no_regression, 'gates': gates}


def metadata():
    from app.config import get_settings
    from app.services.field_table import CLASSIFIER_CACHE_VERSION, _get_prompt_hash
    from docparser.source_model import PARSER_VERSION
    import subprocess

    settings = get_settings()
    return {
        'code': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'working_tree_sha256': hashlib.sha256(subprocess.check_output(['git', 'diff', '--no-ext-diff', '--', 'backend/app/services', 'backend/prompts'])).hexdigest(),
        'parser': PARSER_VERSION, 'classifier': CLASSIFIER_CACHE_VERSION,
        'model': settings.llm_model, 'profile': settings.llm_profile,
        'prompt_sha256': _get_prompt_hash(),
        'settings': {name: getattr(settings, name) for name in (
            'llm_classification_max_tokens', 'llm_max_tokens', 'search_per_branch_top_k',
            'okf_field_table_min_rows', 'okf_max_chunk_chars',
        )},
    }


def raw_inventory(payload, source_id):
    """Independent OOXML values, types, formula/cache state and merge geometry."""
    from lxml import etree
    from docparser.archive_guard import validate_zip
    with tempfile.TemporaryDirectory(prefix="table-inventory-") as temporary:
        guarded = Path(temporary) / "input.xlsx"
        guarded.write_bytes(payload)
        validate_zip(guarded)
    ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    cells, merges = [], []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            tree = etree.fromstring(archive.read('xl/sharedStrings.xml'), parser)
            shared = [''.join(si.itertext()) for si in tree.findall('x:si', ns)]
        import posixpath
        workbook = etree.fromstring(archive.read('xl/workbook.xml'), parser)
        relationships = etree.fromstring(archive.read('xl/_rels/workbook.xml.rels'), parser)
        targets = {rel.get('Id'): rel.get('Target') for rel in relationships
                   if rel.get('Type', '').endswith('/worksheet') and rel.get('TargetMode') != 'External'}
        sheets = []
        for sheet_node in workbook.findall('x:sheets/x:sheet', ns):
            target = targets.get(sheet_node.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'))
            if target:
                sheets.append(target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target))
        for sheet, member in enumerate(sheets):
            tree = etree.fromstring(archive.read(member), parser)
            for cell in tree.findall('.//x:sheetData/x:row/x:c', ns):
                kind = cell.get('t', 'n')
                value = cell.findtext('x:v', namespaces=ns)
                if kind == 'inlineStr':
                    value = ''.join(cell.xpath('x:is//x:t/text()', namespaces=ns))
                elif kind == 's' and value is not None:
                    value = shared[int(value)]
                formula = cell.findtext('x:f', namespaces=ns)
                if value is not None or formula is not None:
                    cells.append({'source_id': source_id, 'sheet': sheet, 'address': cell.get('r'),
                                  'value': value, 'type': kind, 'formula': formula,
                                  'formula_result_missing': formula is not None and value is None})
            merges.extend({'source_id': source_id, 'sheet': sheet, 'range': m.get('ref')}
                          for m in tree.findall('x:mergeCells/x:mergeCell', ns))
    return cells, merges, len(sheets)


def parse_manifest(manifest, base):
    from docparser.xlsx_parser import parse_xlsx
    from app.services.field_table import _parse_row_cells
    report = {'schema_version': 1, 'versions': metadata(), 'documents': [], 'cells': [], 'headers': [], 'gates': {}}
    started = perf_counter()
    for index, document in enumerate(manifest.get('documents', [])):
        original = (base / document['path']).resolve()
        payload = original.read_bytes()
        if hashlib.sha256(payload).hexdigest() != document['sha256']:
            raise ValueError('manifest source hash mismatch')
        if document.get('member'):
            from docparser.archive_guard import validate_zip, validate_member
            validate_zip(original)
            with zipfile.ZipFile(original) as archive:
                validate_member(archive.getinfo(document['member']))
                payload = archive.read(document['member'])
        source = document['source_id']
        document_id = document.get('document_id') or hashlib.sha256(_encoded({'path': document['path'], 'sha256': document['sha256']}).encode()).hexdigest()
        raw, merges, sheet_count = raw_inventory(payload, source)
        raw = [{**cell, 'document_id': document_id} for cell in raw]
        merges = [{**merge, 'document_id': document_id} for merge in merges]
        with tempfile.TemporaryDirectory(prefix='table-probe-') as temporary:
            path = Path(temporary) / 'input.xlsx'
            path.write_bytes(payload)
            tables = []
            sheet_index = -1
            for block in parse_xlsx(path):
                if block.type == 'heading' and block.level == 1:
                    sheet_index += 1
                elif block.type == 'table' and 0 <= sheet_index < sheet_count:
                    tables.append((sheet_index, block))
        # Legacy Markdown has no native coordinates. Map its occupied row order
        # against the independent inventory; malformed rows fail value gates.
        from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
        for sheet, table in tables:
            rows = sorted({int(''.join(filter(str.isdigit, c['address']))) for c in raw
                           if c['sheet'] == sheet and c['value'] is not None and str(c['value']).strip()})
            parsed = [_parse_row_cells(line) for i, line in enumerate(table.text.splitlines()) if i != 1]
            for cell in (c for c in raw if c['sheet'] == sheet):
                col, row = coordinate_from_string(cell['address'])
                if row not in rows:
                    continue
                ordinal, column = rows.index(row), column_index_from_string(col) - 1
                if ordinal < len(parsed) and column < len(parsed[ordinal]):
                    report['cells'].append({**{k: cell[k] for k in ('document_id', 'source_id', 'sheet', 'address')},
                                            'value': parsed[ordinal][column]})
            report['headers'].append({'document_id': document_id, 'source_id': source, 'sheet': sheet,
                                      'value': parsed[0] if parsed else [], 'status': 'legacy_unverified'})
        report['documents'].append({'index': index, 'sha256': document['sha256'], 'raw_cells': raw,
                                    'merges': merges, 'table_count': len(tables)})
        expected = {field: [{**item, 'document_id': document_id} for item in document[field]] if document.get(field) is not None else None
                    for field in ('expected_cells', 'expected_headers')}
        expected = {'cells': expected['expected_cells'], 'headers': expected['expected_headers']}
        current = {field: [item for item in report[field] if item.get('document_id') == document_id]
                   for field in ('cells', 'headers')}
        report['gates'][str(index)] = compare_reports(expected, current)
    report['elapsed_ms'] = round((perf_counter() - started) * 1000, 3)
    report['passed'] = bool(report['gates']) and all(g['passed'] for g in report['gates'].values())
    return report


def run_retrieval(manifest):
    from app.config import get_settings
    from app.services.context_builder import drop_partial_title_matches, drop_unmatched_blocks, merge_and_format, resolve_branches
    from app.services.embedder import Embedder
    from app.services.glossary.expansion import prepare_query
    from app.services.glossary.query_sparse import build_query_sparse
    from app.services.retrieval_hydration import load_visible_retrieval_hits
    from app.services.stopwords import KIND_BM25, get_stopwords
    from app.services.vector_store import VectorStore

    settings, store, embedder = get_settings(), VectorStore(), Embedder()
    results = []
    try:
        for case in manifest.get('cases', []):
            start = perf_counter()
            plan = prepare_query(case['query'], ui_locale='ru', enabled=settings.glossary_query_expansion_enabled, settings=settings)
            groups = getattr(plan, 'strict_groups', ()) or plan.match_groups
            branches = resolve_branches(case.get('mode', 'hybrid'), None, None, settings)
            hits = store.search_composite(
                dense_vec=embedder.embed(plan.dense_query) if 'dense' in branches else None,
                sparse_vec=build_query_sparse(plan, stopwords=get_stopwords(KIND_BM25), settings=settings) if 'bm25' in branches else None,
                tags=case.get('tags'), branches=branches, top_k=settings.search_per_branch_top_k,
            )
            visible, lookup = load_visible_retrieval_hits(hits, **({'exact_groups': groups} if groups else {}))
            merged = merge_and_format(visible, settings, filename_lookup={k: d.get('filename', '') for k, d in lookup.items()},
                                      exact_groups=groups, limit_total_chars=False)[:case.get('top_k', 10)]
            cache, lexical = {}, {}
            merged = drop_unmatched_blocks(merged, case['query'], match_groups=groups, domain_cache=cache, lexical_cache=lexical)
            merged = drop_partial_title_matches(merged, case['query'], match_groups=groups, domain_cache=cache)
            required = case.get('evidence') or []
            def matches(block, evidence, value=False):
                if any(block.get(k, block.get('source_slug') if k == 'slug' else None) != evidence[k]
                       for k in ('doc_id', 'source_id', 'chunk_index', 'slug') if k in evidence):
                    return False
                return not value or all(str(evidence[k]) in block.get('content', '') for k in ('value', 'header') if k in evidence)
            candidate = [any(matches(h.payload, e) for h in hits) for e in required]
            context = [any(matches(b, e, True) for b in merged) for e in required]
            results.append({'id': case['id'], 'category': case['category'], 'candidate_recall': candidate,
                            'context_value_and_source': context, 'passed': bool(required) and all(context),
                            'elapsed_ms': round((perf_counter() - start) * 1000, 3),
                            'raw_candidates': [h.payload for h in hits], 'final_blocks': merged})
    finally:
        store.client.close()
    return {'schema_version': 1, 'versions': metadata(), 'cases': results,
            'passed': bool(results) and all(c['passed'] for c in results),
            'answer_accuracy': None, 'llm_calls': 0, 'llm_tokens': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--mode', required=True, choices=['parse', 'retrieval', 'compare'])
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--after', type=Path)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    if args.mode == 'compare':
        if not args.baseline or not args.after:
            parser.error('compare requires --baseline and --after')
        report = compare_reports(json.loads(args.baseline.read_text(encoding='utf-8-sig')),
                                 json.loads(args.after.read_text(encoding='utf-8-sig')))
    elif args.mode == 'parse':
        report = parse_manifest(manifest, args.manifest.parent)
    else:
        report = run_retrieval(manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
