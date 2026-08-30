"""documents.has_duplicates flag

Revision ID: a7b8c9d0e1f2
Revises: f6a1b2c3d4e5
Create Date: 2026-08-31 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Флаг наличия почти-дубликатов (уровень 2/3) у документа."""
    with op.batch_alter_table('documents') as batch_op:
        batch_op.add_column(sa.Column('has_duplicates', sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('documents') as batch_op:
        batch_op.drop_column('has_duplicates')
