"""Store verified source ranges for generated OKF concepts."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "d6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("okf_concepts", sa.Column("source_spans", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("okf_concepts", "source_spans")
