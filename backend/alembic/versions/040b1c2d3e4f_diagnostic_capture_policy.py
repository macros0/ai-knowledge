"""Add capture policy metadata without relabeling legacy sessions."""
from alembic import op
import sqlalchemy as sa

revision = "040b1c2d3e4f"
down_revision = "030b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("diagnostic_sessions", sa.Column("capture_level", sa.String(12),
                  nullable=False, server_default="detailed"))
    op.add_column("diagnostic_sessions", sa.Column("policy_version", sa.Integer(),
                  nullable=False, server_default="0"))
    op.add_column("diagnostic_sessions", sa.Column("policy_snapshot", sa.JSON(),
                  nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("diagnostic_sessions", "policy_snapshot")
    op.drop_column("diagnostic_sessions", "policy_version")
    op.drop_column("diagnostic_sessions", "capture_level")
