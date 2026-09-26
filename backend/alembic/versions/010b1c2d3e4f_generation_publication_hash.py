"""Bind prepared publication artifacts to a durable checksum."""
from alembic import op
import sqlalchemy as sa

revision = "010b1c2d3e4f"
down_revision = "ff0b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_generations", sa.Column("publication_hash", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("document_generations", "publication_hash")
