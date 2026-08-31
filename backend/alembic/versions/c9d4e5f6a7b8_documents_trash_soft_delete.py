"""documents.deleted_at / deleted_by (trash soft delete)

Revision ID: c9d4e5f6a7b8
Revises: a7b8c9d0e1f2
Create Date: 2026-08-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Корзина (Этап 4a.2): soft delete через deleted_at + deleted_by."""
    with op.batch_alter_table('documents') as batch_op:
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('deleted_by', sa.String(length=255), nullable=True))
    op.create_index(
        op.f('ix_documents_deleted_at'), 'documents', ['deleted_at'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_documents_deleted_at'), table_name='documents')
    with op.batch_alter_table('documents') as batch_op:
        batch_op.drop_column('deleted_by')
        batch_op.drop_column('deleted_at')
