"""stage 7 (D): documents.source_locale

Revision ID: ae5f6a7b8c9d
Revises: 9d4e5f6a7b8c
Create Date: 2026-09-06 20:00:00.000000

Язык исходного документа (эвристика кириллица/латиница при финализации).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ae5f6a7b8c9d'
down_revision: Union[str, Sequence[str], None] = '9d4e5f6a7b8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'documents', sa.Column('source_locale', sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('documents', 'source_locale')
