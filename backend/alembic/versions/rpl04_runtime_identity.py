"""Bind runtime replicas to an explicitly installed common configuration."""
from alembic import op
import sqlalchemy as sa

revision = "rpl04_runtime_identity"
down_revision = "rpl03b_diagnostics"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("replica_runtime_identity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("installation_id", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("storage_receipt_digest", sa.String(71), nullable=False),
        sa.Column("schema_revision", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_replica_runtime_singleton"))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM replica_runtime_identity")):
        raise RuntimeError("Installed replica identity requires coherent recovery")
    op.drop_table("replica_runtime_identity")
