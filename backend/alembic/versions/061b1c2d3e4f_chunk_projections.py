"""Generation-scoped materialized generic chunk projections.

Nullable generation version leaves all legacy rows unchanged. Downgrade refuses
any generation using the new contract instead of deleting its representations.
"""
from alembic import op
import sqlalchemy as sa
revision='061b1c2d3e4f'
down_revision='060b1c2d3e4f'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('document_generations',sa.Column('chunk_projection_version',sa.Integer(),nullable=True))
    op.create_table('document_chunk_projections',
        sa.Column('doc_id',sa.String(16),sa.ForeignKey('documents.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('generation_id',sa.String(32),sa.ForeignKey('document_generations.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('chunk_index',sa.Integer(),primary_key=True),
        sa.Column('source_id',sa.String(255),nullable=True),sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('source_hash',sa.String(64),nullable=False),sa.Column('title_hash',sa.String(64),nullable=False),
        sa.Column('projection_hash',sa.String(64),nullable=False),
        sa.Column('fragment_count',sa.Integer(),nullable=False,server_default='0'),
        sa.Column('complete',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.CheckConstraint('chunk_index >= 0 AND fragment_count >= 0 AND version > 0',name='ck_chunk_projection_bounds'))
    op.create_table('document_chunk_fragments',
        sa.Column('doc_id',sa.String(16),primary_key=True),sa.Column('generation_id',sa.String(32),primary_key=True),
        sa.Column('chunk_index',sa.Integer(),primary_key=True),sa.Column('ordinal',sa.Integer(),primary_key=True),
        sa.Column('point_id',sa.String(36),nullable=False,unique=True),
        sa.Column('descriptor',sa.JSON(),nullable=False),sa.Column('content',sa.Text(),nullable=False),
        sa.Column('text_hash',sa.String(64),nullable=False),
        sa.ForeignKeyConstraint(['doc_id','generation_id','chunk_index'],
            ['document_chunk_projections.doc_id','document_chunk_projections.generation_id','document_chunk_projections.chunk_index'],ondelete='CASCADE'),
        sa.CheckConstraint('ordinal >= 0',name='ck_chunk_fragment_ordinal'))

def downgrade():
    if op.get_bind().execute(sa.text('SELECT COUNT(*) FROM document_generations WHERE chunk_projection_version IS NOT NULL')).scalar():
        raise RuntimeError('Cannot downgrade generations using chunk projections')
    op.drop_table('document_chunk_fragments')
    op.drop_table('document_chunk_projections')
    op.drop_column('document_generations','chunk_projection_version')
