"""developments.version (optimistic locking for the developments reference)

Revision ID: e5a6b7c8d9f0
Revises: d1e2f3a4b5c6
Create Date: 2026-09-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a6b7c8d9f0'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Оптимистическая блокировка справочника разработок: целочисленный version."""
    with op.batch_alter_table('developments') as batch_op:
        batch_op.add_column(
            sa.Column('version', sa.Integer(), nullable=False, server_default='1')
        )


def downgrade() -> None:
    with op.batch_alter_table('developments') as batch_op:
        batch_op.drop_column('version')
