"""Batch validation preserves strict coordinates, ordering and canonical payloads."""
import copy
import pytest
from app.models.schemas import TableRef
from app.services import table_retrieval

BASE = {'generation_id':'a'*32,'table_id':'b'*32,'source_id':'root','row_number':2,'columns':[1,3]}

@pytest.mark.parametrize('patch', [
 {'row_number':True}, {'row_number':'2'}, {'row_number':0}, {'columns':[1,1]},
 {'columns':['1']}, {'columns':[]}, {'columns':[16385]}, {'generation_id':'wrong'},
 {'source_id':'root/../foreign'}, {'extra':'forged'}, {'text_start':0},
 {'text_start':0,'text_end':3}, {'columns':[1],'text_start':3,'text_end':3},
])
def test_batch_validation_rejects_entire_mixed_list_with_invalid_coordinate(patch):
 refs=[copy.deepcopy(BASE),{**copy.deepcopy(BASE),**patch},copy.deepcopy(BASE)]
 with pytest.raises(ValueError):
  [TableRef.model_validate(r) for r in refs]
 with pytest.raises(ValueError):
  table_retrieval._validated_table_refs(refs)


def test_batch_validation_keeps_duplicate_physical_refs_order_bounds_and_defaults():
 refs=[copy.deepcopy(BASE),{**BASE,'row_number':3,'columns':[3,1]},copy.deepcopy(BASE),
       {**BASE,'columns':[1],'text_start':0,'text_end':3},
       {**BASE,'text_start':None,'text_end':None}]
 original=copy.deepcopy(refs)
 expected=[TableRef.model_validate(r) for r in refs]
 actual=table_retrieval._validated_table_refs(refs)
 assert actual==expected
 assert table_retrieval._TABLE_REFS.dump_python(actual,mode='json')==[r.model_dump(mode='json') for r in expected]
 assert refs==original and len(actual)==5
