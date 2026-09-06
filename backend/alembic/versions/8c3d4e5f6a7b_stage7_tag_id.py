"""stage 7 (B): surrogate tag_id + переводы справочников

Revision ID: 8c3d4e5f6a7b
Revises: 7a1b2c3d4e5f
Create Date: 2026-09-06 12:00:00.000000

tags.name (текстовый PK) → tags.id (суррогатный) + canonical_text (unique) +
canonical_locale; document_tags.tag → document_tags.tag_id (FK); новые таблицы
tag_translations / development_translations / attribute_value_translations
(сид ru из канонических колонок). Qdrant payload и okf_concepts.tags НЕ трогаются
(канонический текст) — реиндекс не нужен (Этап 7, решение §0 плана).
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8c3d4e5f6a7b'
down_revision: Union[str, Sequence[str], None] = '7a1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    # --- 1. tags: name (PK) → id + canonical_text (полная пересборка) ---
    op.create_table(
        'tags_new',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('canonical_text', sa.String(length=255), nullable=False),
        sa.Column('canonical_locale', sa.String(length=16), nullable=False),
        sa.Column('merged_into_id', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('canonical_text', name='uq_tags_canonical_text'),
    )
    conn.execute(
        sa.text(
            "INSERT INTO tags_new (canonical_text, canonical_locale, created_at) "
            "SELECT name, 'ru', :now FROM tags"
        ),
        {"now": now},
    )
    op.drop_table('tags')
    op.rename_table('tags_new', 'tags')
    # Self-FK merged_into_id — добавляется ПОСЛЕ переименования (иначе ссылался бы
    # на старую таблицу tags, которую мы только что удалили). SQLite не умеет
    # ALTER ADD CONSTRAINT и не энфорсит FK — пропускаем там (паттерн create_all).
    if conn.dialect.name == 'postgresql':
        op.create_foreign_key(
            'fk_tags_merged_into', 'tags', 'tags', ['merged_into_id'], ['id'],
            ondelete='SET NULL'
        )
    # Орфан-теги: document_tags.tag, отсутствующий в пуле (тег на документе после
    # чистки пула — баг 06.09.2026 «тег ааа»). Создаём строку tags, иначе JOIN
    # при переносе document_tags потеряет связь.
    conn.execute(
        sa.text(
            "INSERT INTO tags (canonical_text, canonical_locale, created_at) "
            "SELECT DISTINCT dt.tag, 'ru', :now FROM document_tags dt "
            "WHERE NOT EXISTS (SELECT 1 FROM tags t WHERE t.canonical_text = dt.tag)"
        ),
        {"now": now},
    )

    # --- 2. tag_translations ---
    op.create_table(
        'tag_translations',
        sa.Column('tag_id', sa.Integer(), nullable=False),
        sa.Column('locale', sa.String(length=16), nullable=False),
        sa.Column('text', sa.String(length=255), nullable=False),
        sa.Column('is_machine_translated', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('reviewed_by', sa.String(length=255), nullable=True),
        sa.Column('translated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('tag_id', 'locale'),
    )

    # --- 3. document_tags: (doc_id, tag) → (doc_id, tag_id) ---
    op.create_table(
        'document_tags_new',
        sa.Column('doc_id', sa.String(length=16), nullable=False),
        sa.Column('tag_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['doc_id'], ['documents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('doc_id', 'tag_id'),
    )
    conn.execute(
        sa.text(
            "INSERT INTO document_tags_new (doc_id, tag_id) "
            "SELECT dt.doc_id, t.id FROM document_tags dt "
            "JOIN tags t ON t.canonical_text = dt.tag"
        )
    )
    op.drop_table('document_tags')
    op.rename_table('document_tags_new', 'document_tags')

    # --- 4. development_translations (сид ru = developments.name) ---
    op.create_table(
        'development_translations',
        sa.Column('development_id', sa.Integer(), nullable=False),
        sa.Column('locale', sa.String(length=16), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('is_machine_translated', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('reviewed_by', sa.String(length=255), nullable=True),
        sa.Column('translated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['development_id'], ['developments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('development_id', 'locale'),
    )
    conn.execute(
        sa.text(
            "INSERT INTO development_translations (development_id, locale, name, is_machine_translated) "
            "SELECT id, 'ru', name, false FROM developments"
        )
    )

    # --- 5. attribute_value_translations (сид ru = attribute_values.label) ---
    op.create_table(
        'attribute_value_translations',
        sa.Column('attribute_value_id', sa.Integer(), nullable=False),
        sa.Column('locale', sa.String(length=16), nullable=False),
        sa.Column('label', sa.String(length=255), nullable=False),
        sa.Column('is_machine_translated', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('reviewed_by', sa.String(length=255), nullable=True),
        sa.Column('translated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['attribute_value_id'], ['attribute_values.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('attribute_value_id', 'locale'),
    )
    conn.execute(
        sa.text(
            "INSERT INTO attribute_value_translations (attribute_value_id, locale, label, is_machine_translated) "
            "SELECT id, 'ru', label, false FROM attribute_values WHERE label IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.drop_table('attribute_value_translations')
    op.drop_table('development_translations')
    op.drop_table('tag_translations')
    # document_tags: tag_id → tag (текст)
    op.create_table(
        'document_tags_old',
        sa.Column('doc_id', sa.String(length=16), nullable=False),
        sa.Column('tag', sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(['doc_id'], ['documents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('doc_id', 'tag'),
    )
    op.get_bind().execute(
        sa.text(
            "INSERT INTO document_tags_old (doc_id, tag) "
            "SELECT dt.doc_id, t.canonical_text FROM document_tags dt JOIN tags t ON t.id = dt.tag_id"
        )
    )
    op.drop_table('document_tags')
    op.rename_table('document_tags_old', 'document_tags')
    # tags: id → name
    op.create_table(
        'tags_old',
        sa.Column('name', sa.String(length=255), primary_key=True),
    )
    op.get_bind().execute(
        sa.text("INSERT INTO tags_old (name) SELECT canonical_text FROM tags")
    )
    op.drop_table('tags')
    op.rename_table('tags_old', 'tags')
