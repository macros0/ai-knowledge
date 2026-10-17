"""Dialect-specific generated identifier projection; canonical JSON stays intact."""
from sqlalchemy import JSON
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ColumnElement


class TableCellsProjection(ColumnElement):
    inherit_cache = True
    type = JSON()


@compiles(TableCellsProjection)
def portable_projection(element, compiler, **kwargs):
    return 'cells'


@compiles(TableCellsProjection, 'postgresql')
def postgres_projection(element, compiler, **kwargs):
    # Superset prefilter: Python strip/fullmatch remains authoritative, including Unicode whitespace.
    return 'CASE WHEN json_typeof(cells) = \'object\' THEN jsonb_path_query_array(cells::jsonb,\n\'$.keyvalue() ? (@.value.type() == "object" && @.value.display_text.type() == "string" && @.value.display_text like_regex "[0-9]{3}")\') ELSE \'[]\'::jsonb END'
