"""stage 4: developments, attribute_values, dedup columns, lsh buckets

Revision ID: e3f1a2b4c5d6
Revises: b7f2c9d3e4a1
Create Date: 2026-08-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3f1a2b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'b7f2c9d3e4a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('developments',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('number', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('module', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number')
    )

    op.create_table('attribute_values',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('attribute_key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.String(length=255), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=True),
    sa.Column('sort_order', sa.Integer(), nullable=True),
    sa.Column('org_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('attribute_key', 'value', 'org_id', name='uq_attribute_values_key_value_org')
    )
    op.create_index(op.f('ix_attribute_values_attribute_key'), 'attribute_values', ['attribute_key'], unique=False)

    op.create_table('document_lsh_buckets',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('doc_id', sa.String(length=16), nullable=False),
    sa.Column('variant', sa.String(length=16), nullable=False),
    sa.Column('band_index', sa.Integer(), nullable=False),
    sa.Column('bucket_hash', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['doc_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('doc_id', 'variant', 'band_index', name='uq_lsh_doc_variant_band')
    )
    op.create_index(op.f('ix_document_lsh_buckets_doc_id'), 'document_lsh_buckets', ['doc_id'], unique=False)
    op.create_index('ix_lsh_lookup', 'document_lsh_buckets', ['variant', 'band_index', 'bucket_hash'], unique=False)

    # Batch mode: портабельно добавляет колонки + FK + индексы (SQLite делает
    # copy-and-move, PostgreSQL — ALTER TABLE ADD COLUMN/CONSTRAINT).
    with op.batch_alter_table('documents') as batch_op:
        batch_op.add_column(sa.Column('development_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('development_confidence', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('development_confirmed_by', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('development_suggestion', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('file_hash', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('content_hash', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('minhash', sa.JSON(), nullable=True))
        batch_op.create_foreign_key('fk_documents_development_id', 'developments', ['development_id'], ['id'])
        batch_op.create_index(op.f('ix_documents_file_hash'), ['file_hash'], unique=False)
        batch_op.create_index(op.f('ix_documents_content_hash'), ['content_hash'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('documents') as batch_op:
        batch_op.drop_index(op.f('ix_documents_content_hash'))
        batch_op.drop_index(op.f('ix_documents_file_hash'))
        batch_op.drop_constraint('fk_documents_development_id', type_='foreignkey')
        batch_op.drop_column('minhash')
        batch_op.drop_column('content_hash')
        batch_op.drop_column('file_hash')
        batch_op.drop_column('development_suggestion')
        batch_op.drop_column('development_confirmed_by')
        batch_op.drop_column('development_confidence')
        batch_op.drop_column('development_id')

    op.drop_index('ix_lsh_lookup', table_name='document_lsh_buckets')
    op.drop_index(op.f('ix_document_lsh_buckets_doc_id'), table_name='document_lsh_buckets')
    op.drop_table('document_lsh_buckets')

    op.drop_index(op.f('ix_attribute_values_attribute_key'), table_name='attribute_values')
    op.drop_table('attribute_values')

    op.drop_table('developments')
