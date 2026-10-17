"""Decode canonical SQL JSON references once while preserving strict root semantics."""
import copy
import json
import pytest
from app.models.schemas import TableRef
from app.services import retrieval_hydration,table_retrieval

BASE={'generation_id':'a'*32,'table_id':'b'*32,'source_id':'root','row_number':2,'columns':[1,3]}

@pytest.mark.parametrize('value', [None,[],{},False,0,0.0,''])
def test_encoded_falsey_canonical_refs_stay_legacy(value):
 assert retrieval_hydration._canonical_table_value('doc','root','text',json.dumps(value),encoded=True) is None
 assert retrieval_hydration._canonical_table_value('doc','root','text',value) is None

@pytest.mark.parametrize('value', ['[]',json.dumps([BASE]),True,1,{'bad':'root'},['invalid'],[False]])
def test_truthy_wrong_roots_and_json_strings_cannot_forge_valid_refs(value):
 stored=retrieval_hydration._canonical_table_value('doc','root','text',json.dumps(value),encoded=True)
 assert stored is not None
 with pytest.raises(ValueError):
  table_retrieval._validated_table_refs(value)
 with pytest.raises(ValueError):
  table_retrieval._validated_table_refs(stored.table_refs,encoded=stored.table_refs_json)

@pytest.mark.parametrize('patch', [
 {'row_number':True}, {'row_number':'2'}, {'row_number':2.0}, {'columns':[1,1]},
 {'columns':['1']}, {'columns':[]}, {'source_id':'root/../foreign'}, {'extra':'forged'},
 {'text_start':0}, {'text_start':0,'text_end':3},
])
def test_direct_json_validator_keeps_strict_type_and_range_rejections(patch):
 refs=[copy.deepcopy(BASE),{**copy.deepcopy(BASE),**patch}]
 with pytest.raises(ValueError):
  table_retrieval._validated_table_refs(refs)
 with pytest.raises(ValueError):
  table_retrieval._validated_table_refs(json.dumps(refs),encoded=True)


def test_sql_json_payload_exact_coordinates_order_duplicates_bounds_and_provenance():
 refs=[copy.deepcopy(BASE),{**BASE,'row_number':3,'columns':[3,1]},copy.deepcopy(BASE),
       {**BASE,'columns':[1],'text_start':0,'text_end':3}]
 original=copy.deepcopy(refs)
 expected=[TableRef.model_validate(r).model_dump(mode='json') for r in refs]
 for encoded,value in [(False,refs),(True,json.dumps(refs,indent=2))]:
  stored=retrieval_hydration._canonical_table_value('doc','root','unchanged',value,encoded=encoded)
  actual=table_retrieval._validated_table_refs(stored.table_refs,encoded=stored.table_refs_json)
  assert table_retrieval._TABLE_REFS.dump_python(actual,mode='json')==expected
  assert stored.doc_id=='doc' and stored.source_id=='root' and stored.content=='unchanged'
 assert refs==original


def test_whitespace_empty_encoded_array_stays_legacy():
 assert retrieval_hydration._canonical_table_value('doc','root','text','[ \n ]',encoded=True) is None

@pytest.mark.parametrize('raw', [' []','[] ','\n[ ]\n',' \r\n [ \n ] \t'])
def test_outer_whitespace_empty_encoded_array_stays_legacy(raw):
 assert retrieval_hydration._canonical_table_value('doc','root','text',raw,encoded=True) is None
