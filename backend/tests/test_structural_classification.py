import pytest
from app.services.llm_scheduler import LLMCancelled
from app.services.table_classification import classify_table
from app.services.table_projection import ProjectionLimits
from docparser.table_model import CellRange, TableColumn, TableDescriptor, TableRow, TableCell


def fixture_table():
 d=TableDescriptor('root','b'*32,0,'Small',CellRange(0,1,1,2,2),[1],
   [TableColumn(1,'A',['Code']),TableColumn(2,'B',['Value'])])
 rows=[TableRow(2,{1:TableCell('A2','K','K','string'),2:TableCell('B2',7,'7','number')})]
 return d,rows


def test_structural_classification_cancellation_is_not_fallback():
 class LLM:
  def chat_json(self,*a,**kw): raise LLMCancelled('cancelled')
 d,rows=fixture_table()
 with pytest.raises(LLMCancelled): classify_table(d,rows,ProjectionLimits(),LLM())


def test_structural_cache_includes_preview_prompt_model_and_versions(monkeypatch,tmp_path):
 import app.services.table_classification as module
 from app.config import Settings
 settings=Settings(_env_file=None,data_dir=tmp_path,llm_model='test/cache')
 monkeypatch.setattr(module,'get_settings',lambda:settings,raising=False)
 class LLM:
  calls=0
  def chat_json(self,*a,**kw):
   self.calls+=1
   return {'concept_per_row':True,'title_col':1,'concept_type':'reference','extraction_mode':'per_row'}
 llm=LLM(); d,rows=fixture_table()
 classify_table(d,rows,ProjectionLimits(),llm,system_prompt='first')
 classify_table(d,rows,ProjectionLimits(),llm,system_prompt='first')
 assert llm.calls==1
 classify_table(d,rows,ProjectionLimits(),llm,system_prompt='second')
 assert llm.calls==2
 rows[0].cells[2]=__import__('dataclasses').replace(rows[0].cells[2],display_text='changed')
 classify_table(d,rows,ProjectionLimits(),llm,system_prompt='second')
 assert llm.calls==3
