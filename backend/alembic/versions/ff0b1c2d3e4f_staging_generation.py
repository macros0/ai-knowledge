"""Bind generation checkpoints to their immutable publication attempt."""
from alembic import op
import sqlalchemy as sa

revision = "ff0b1c2d3e4f"
down_revision = "fe0b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_staging", sa.Column("generation_id", sa.String(32), nullable=True))


def downgrade():
    op.drop_column("document_staging", "generation_id")
