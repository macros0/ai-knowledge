"""Independent audit outbox and functional import/migration history."""
from alembic import op
from app.services.audit_schema import upgrade_audit_schema

revision = "067b1c2d3e4f"
down_revision = "066b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    upgrade_audit_schema(op.get_bind())


def downgrade():
    raise RuntimeError("Audit v2 requires forward repair or an isolated pre-migration backup restore")
