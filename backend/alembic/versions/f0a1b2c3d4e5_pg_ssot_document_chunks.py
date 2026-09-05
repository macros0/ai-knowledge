"""document_chunks + okf_attachments/okf_concepts columns (Этап 2b: PG SSOT)

Revision ID: f0a1b2c3d4e5
Revises: e5a6b7c8d9f0
Create Date: 2026-09-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f0a1b2c3d4e5'
down_revision: Union[str, Sequence[str], None] = 'e5a6b7c8d9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Финальные чанки + провенанс концептов + вложения как рабочие сущности БД."""
    op.create_table(
        'document_chunks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('doc_id', sa.String(length=16), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('section_title', sa.String(length=1024), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=True),
        sa.Column('char_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['doc_id'], ['documents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('doc_id', 'chunk_index', name='uq_document_chunks_doc_index'),
    )
    op.create_index(
        op.f('ix_document_chunks_doc_id'), 'document_chunks', ['doc_id'], unique=False
    )

    with op.batch_alter_table('okf_attachments') as batch_op:
        batch_op.add_column(sa.Column('content_type', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('size', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('sha256', sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column('is_processable', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column('extraction_status', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('extracted_chars', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_unique_constraint(
            'uq_okf_attachments_doc_saved_path', ['doc_id', 'saved_path']
        )

    with op.batch_alter_table('okf_concepts') as batch_op:
        batch_op.add_column(sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('model_id', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('prompt_version', sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('okf_concepts') as batch_op:
        batch_op.drop_column('prompt_version')
        batch_op.drop_column('model_id')
        batch_op.drop_column('generated_at')

    with op.batch_alter_table('okf_attachments') as batch_op:
        batch_op.drop_constraint('uq_okf_attachments_doc_saved_path', type_='unique')
        batch_op.drop_column('created_at')
        batch_op.drop_column('error')
        batch_op.drop_column('processed_at')
        batch_op.drop_column('extracted_chars')
        batch_op.drop_column('extraction_status')
        batch_op.drop_column('is_processable')
        batch_op.drop_column('sha256')
        batch_op.drop_column('size')
        batch_op.drop_column('content_type')

    op.drop_index(op.f('ix_document_chunks_doc_id'), table_name='document_chunks')
    op.drop_table('document_chunks')
