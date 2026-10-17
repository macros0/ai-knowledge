"""Add a derived binary cell projection without changing canonical JSON.

PostgreSQL stores JSONB; SQLite uses a virtual generated JSON column.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '060b1c2d3e4f'
down_revision = '050b1c2d3e4f'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('document_table_rows', sa.Column('cells_projection',
        sa.JSON().with_variant(JSONB(), 'postgresql'), sa.Computed('CASE WHEN json_typeof(cells) = \'object\' THEN jsonb_path_query_array(cells::jsonb,\n\'$.keyvalue() ? (@.value.type() == "object" && @.value.display_text.type() == "string" && @.value.display_text like_regex "[0-9]{3}")\') ELSE \'[]\'::jsonb END' if op.get_bind().dialect.name == 'postgresql' else 'cells'), nullable=True))


def downgrade():
    op.drop_column('document_table_rows', 'cells_projection')
