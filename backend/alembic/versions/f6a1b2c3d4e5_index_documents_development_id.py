"""index documents.development_id

Revision ID: f6a1b2c3d4e5
Revises: e3f1a2b4c5d6
Create Date: 2026-08-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f6a1b2c3d4e5'
down_revision: Union[str, Sequence[str], None] = 'e3f1a2b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Индекс по documents.development_id — для коррелированного COUNT при
    сортировке/выводе счётчика документов разработки."""
    op.create_index(
        op.f('ix_documents_development_id'), 'documents', ['development_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_documents_development_id'), table_name='documents')
