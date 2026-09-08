"""documents.problem — диагностический код неполноты

Revision ID: b8c9d0e1f2a3
Revises: ae5f6a7b8c9d
Create Date: 2026-09-08 12:00:00.000000

Колонка появилась в моделях вместе с problem-кодами (a0736b5, 03.09.2026), но
миграции не получила. В dev это не видно: там схему создаёт init_db()
(create_all). В production init_db() намеренно пропускается, схему ведёт только
Alembic — и любое чтение документов (select(Document) тянет все маппленные
колонки) падало с UndefinedColumn.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'ae5f6a7b8c9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('documents', sa.Column('problem', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('documents', 'problem')
