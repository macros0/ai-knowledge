"""Store the original language of development names and attribute labels.

Historical origins were not recorded: und means unknown, never an assumed ru.
Existing tag origins are preserved.
"""
from alembic import op
import sqlalchemy as sa

revision = "f8d9e0a1b2c3"
down_revision = "e7c8d9f0a1b2"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("developments", "attribute_values"):
        op.add_column(table, sa.Column("canonical_locale", sa.String(16), nullable=False, server_default="und"))


def downgrade():
    for table in ("attribute_values", "developments"):
        op.drop_column(table, "canonical_locale")
