"""stage 7 (A): locales + stopwords

Revision ID: 7a1b2c3d4e5f
Revises: f0a1b2c3d4e5
Create Date: 2026-09-06 00:00:00.000000

"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a1b2c3d4e5f'
down_revision: Union[str, Sequence[str], None] = 'f0a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Фаза A (Этап 7): таблицы locales/stopwords + сид.

    Сид стоп-слов — из единственного источника (services/stopwords.default_stopwords),
    который, в свою очередь, берёт ru-наборы из замороженных констант
    sparse._STOPWORDS / context_builder._MARKER_STOPWORDS. Это тот же источник,
    что и ensure_seeded() для dev (create_all без Alembic-сида) — синхронизированы.
    """
    op.create_table(
        'locales',
        sa.Column('code', sa.String(length=16), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('ui_dictionary_version', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('code'),
    )

    op.create_table(
        'stopwords',
        sa.Column('locale', sa.String(length=16), nullable=False),
        sa.Column('word', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['locale'], ['locales.code'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('locale', 'word', 'kind'),
    )

    from app.services.stopwords import DEFAULT_LOCALES, default_stopwords

    now = datetime.now(timezone.utc)

    locales_table = sa.table(
        'locales',
        sa.column('code', sa.String),
        sa.column('name', sa.String),
        sa.column('status', sa.String),
        sa.column('ui_dictionary_version', sa.Integer),
        sa.column('created_at', sa.DateTime(timezone=True)),
        sa.column('updated_at', sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        locales_table,
        [
            {
                'code': code,
                'name': name,
                'status': status,
                'ui_dictionary_version': None,
                'created_at': now,
                'updated_at': now,
            }
            for code, name, status in DEFAULT_LOCALES
        ],
    )

    stopwords_table = sa.table(
        'stopwords',
        sa.column('locale', sa.String),
        sa.column('word', sa.String),
        sa.column('kind', sa.String),
        sa.column('created_at', sa.DateTime(timezone=True)),
        sa.column('created_by', sa.String),
        sa.column('updated_at', sa.DateTime(timezone=True)),
        sa.column('updated_by', sa.String),
    )
    rows = []
    for locale, kinds in default_stopwords().items():
        for kind, words in kinds.items():
            for word in sorted(words):
                rows.append(
                    {
                        'locale': locale,
                        'word': word,
                        'kind': kind,
                        'created_at': now,
                        'created_by': 'seed',
                        'updated_at': now,
                        'updated_by': None,
                    }
                )
    op.bulk_insert(stopwords_table, rows)


def downgrade() -> None:
    op.drop_table('stopwords')
    op.drop_table('locales')
