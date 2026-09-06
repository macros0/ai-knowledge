"""stage 7 (C): ui_dictionaries

Revision ID: 9d4e5f6a7b8c
Revises: 8c3d4e5f6a7b
Create Date: 2026-09-06 18:00:00.000000

Runtime-override UI-словарей: таблица версий ui_dictionaries (locale, version,
data JSON) — актуальная версия указывается в locales.ui_dictionary_version.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9d4e5f6a7b8c'
down_revision: Union[str, Sequence[str], None] = '8c3d4e5f6a7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ui_dictionaries',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('locale', sa.String(length=16), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('uploaded_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['locale'], ['locales.code'], ondelete='CASCADE'),
        sa.UniqueConstraint('locale', 'version', name='uq_ui_dictionaries_locale_version'),
    )
    op.create_index('ix_ui_dictionaries_locale', 'ui_dictionaries', ['locale'])


def downgrade() -> None:
    op.drop_index('ix_ui_dictionaries_locale', table_name='ui_dictionaries')
    op.drop_table('ui_dictionaries')
