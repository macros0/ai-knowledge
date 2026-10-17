"""Add generation-scoped tables and optional canonical references."""
from alembic import op
import sqlalchemy as sa

revision = '050b1c2d3e4f'
down_revision = '040b1c2d3e4f'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('document_generations', sa.Column('table_schema_version', sa.Integer(), nullable=True))
    op.create_table('document_tables',
        sa.Column('doc_id', sa.String(16), sa.ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('generation_id', sa.String(32), sa.ForeignKey('document_generations.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('table_id', sa.String(32), primary_key=True),
        sa.Column('source_id', sa.String(255), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('descriptor', sa.JSON(), nullable=False),
        sa.Column('quality', sa.JSON(), nullable=False))
    op.create_table('document_table_rows',
        sa.Column('doc_id', sa.String(16), primary_key=True),
        sa.Column('generation_id', sa.String(32), primary_key=True),
        sa.Column('table_id', sa.String(32), primary_key=True),
        sa.Column('row_number', sa.Integer(), primary_key=True),
        sa.Column('cells', sa.JSON(), nullable=False),
        sa.Column('hidden', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['doc_id', 'generation_id', 'table_id'],
            ['document_tables.doc_id', 'document_tables.generation_id', 'document_tables.table_id'],
            ondelete='CASCADE', name='fk_document_table_rows_table'))
    op.add_column('okf_concepts', sa.Column('table_refs', sa.JSON(), nullable=True))
    op.add_column('document_chunks', sa.Column('table_refs', sa.JSON(), nullable=True))


def downgrade():
    # Downgrade is allowed only before any canonical table data was written.
    if op.get_bind().execute(sa.text('SELECT COUNT(*) FROM document_tables')).scalar():
        raise RuntimeError('Cannot downgrade after canonical table data has been written')
    op.drop_column('document_generations', 'table_schema_version')
    op.drop_column('document_chunks', 'table_refs')
    op.drop_column('okf_concepts', 'table_refs')
    op.drop_table('document_table_rows')
    op.drop_table('document_tables')
