"""NOT NULL там, где модели его объявляют

Revision ID: c1d2e3f4a5b6
Revises: b8c9d0e1f2a3
Create Date: 2026-09-08 14:00:00.000000

Тот же класс расхождения, что пропустил documents.problem: модели объявляют
колонку NOT NULL, а миграция создала её nullable. Не ломает (значения ставит
Python — default=_utcnow / default=0), но dev-схема (create_all) и prod-схема
(Alembic) физически разные, и compare_metadata видит дрейф.

Перед SET NOT NULL остатки NULL заполняются: колонки заполняет приложение, но
миграция не должна падать на строке, попавшей в базу мимо него.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (таблица, колонка, тип, чем заполнять оставшиеся NULL)
_COLUMNS = (
    ('chat_messages', 'created_at', sa.DateTime(timezone=True), 'CURRENT_TIMESTAMP'),
    ('chat_sessions', 'created_at', sa.DateTime(timezone=True), 'CURRENT_TIMESTAMP'),
    ('chat_sessions', 'updated_at', sa.DateTime(timezone=True), 'CURRENT_TIMESTAMP'),
    ('document_chunks', 'created_at', sa.DateTime(timezone=True), 'CURRENT_TIMESTAMP'),
    ('okf_attachments', 'created_at', sa.DateTime(timezone=True), 'CURRENT_TIMESTAMP'),
    ('attribute_values', 'sort_order', sa.Integer(), '0'),
)


def upgrade() -> None:
    for table, column, type_, filler in _COLUMNS:
        op.execute(f'UPDATE {table} SET {column} = {filler} WHERE {column} IS NULL')
        # batch_alter_table — ради SQLite: там ALTER COLUMN нет, таблица
        # пересоздаётся. На Postgres разворачивается в обычный ALTER.
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(column, existing_type=type_, nullable=False)


def downgrade() -> None:
    for table, column, type_, _filler in _COLUMNS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(column, existing_type=type_, nullable=True)
