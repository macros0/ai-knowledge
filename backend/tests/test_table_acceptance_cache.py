"""Observed classifier cache decisions, independent from warm/cold labels."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest


def api():
    path=Path(__file__).parents[1]/'test_scripts/table_acceptance_cache.py'
    assert path.exists(), 'actual cache hit/miss capture is missing'
    spec=importlib.util.spec_from_file_location('r2_cache',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def real_table():
    from docparser.table_model import CellRange,TableColumn,TableDescriptor,TableRow,TableCell
    descriptor=TableDescriptor('root','b'*32,0,'Small',CellRange(0,1,1,2,2),[1],
        [TableColumn(1,'A',['Code']),TableColumn(2,'B',['Value'])])
    return descriptor,[TableRow(2,{1:TableCell('A2','K','K','string'),2:TableCell('B2',0,'0','number')})]


def test_real_structural_cold_miss_then_warm_hit_and_alias_restore(monkeypatch,tmp_path):
    from app.config import Settings
    from app.services import field_table,table_classification,table_pipeline
    from app.services.table_projection import ProjectionLimits
    monkeypatch.setattr(table_classification,'get_settings',lambda:Settings(_env_file=None,data_dir=tmp_path,llm_model='test/cache'))
    field_table.set_cache_fresh_since(None)
    llm=SimpleNamespace(chat_json=lambda *a,**kw:{'concept_per_row':True,'title_col':1,'concept_type':'reference','extraction_mode':'per_row'})
    original=table_pipeline.classify_table;descriptor,rows=real_table()
    with api().ClassifierCacheCapture(legacy=field_table,structural_targets=(table_classification,table_pipeline)) as capture:
        first=table_pipeline.classify_table(descriptor,rows,ProjectionLimits(),llm)
        second=table_classification.classify_table(descriptor,rows,ProjectionLimits(),llm)
        assert first==second
        assert capture.result()['structural']=={'lookups':2,'hits':1,'misses':1,'disabled':0,'fallbacks':0,'errors':0,'provider_calls':1}
    assert table_pipeline.classify_table is original


def test_legacy_records_returned_hit_and_miss_and_restores_function():
    def original(key):return None if key=='missing' else object()
    legacy=SimpleNamespace(_load_cached_classification=original)
    with api().ClassifierCacheCapture(legacy=legacy,structural_targets=()) as capture:
        assert legacy._load_cached_classification('missing') is None
        assert legacy._load_cached_classification('present') is not None
        assert capture.result()['legacy']=={'lookups':2,'hits':1,'misses':1,'errors':0}
    assert legacy._load_cached_classification is original


def test_disabled_structural_llm_is_not_called_a_cache_hit():
    target=SimpleNamespace(classify_table=lambda a,b,c,llm=None:SimpleNamespace(cause_code=None))
    with api().ClassifierCacheCapture(legacy=None,structural_targets=(target,)) as capture:
        target.classify_table(None,None,None)
        assert capture.result()['structural']['disabled']==1
        assert capture.result()['structural']['hits']==0


def test_structural_fallback_is_miss_with_actual_provider_call():
    def classify(a,b,c,llm=None):
        llm.chat_json('system','preview')
        return SimpleNamespace(cause_code='classifier_invalid_response')
    target=SimpleNamespace(classify_table=classify)
    with api().ClassifierCacheCapture(legacy=None,structural_targets=(target,)) as capture:
        target.classify_table(None,None,None,llm=SimpleNamespace(chat_json=lambda *a:None))
        assert capture.result()['structural']['misses']==1 and capture.result()['structural']['fallbacks']==1


def test_cancellation_preserves_exception_and_records_attempt():
    def fail(*a):raise InterruptedError('cancelled')
    target=SimpleNamespace(classify_table=lambda a,b,c,llm=None:llm.chat_json())
    with api().ClassifierCacheCapture(legacy=None,structural_targets=(target,)) as capture:
        with pytest.raises(InterruptedError):target.classify_table(None,None,None,SimpleNamespace(chat_json=fail))
        assert capture.result()['structural']['errors']==1 and capture.result()['structural']['provider_calls']==1
