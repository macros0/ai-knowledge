"""stage 7 (D): documents.source_locale_source

Revision ID: e7c8d9f0a1b2
Revises: c1d2e3f4a5b6
Create Date: 2026-09-10 00:00:00.000000

Источник значения source_locale: 'detected' (авто py3langid) | 'manual'
(ручная правка, защищена от перезаписи при regenerate) | None.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7c8d9f0a1b2'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'documents', sa.Column('source_locale_source', sa.String(length=8), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('documents', 'source_locale_source')
