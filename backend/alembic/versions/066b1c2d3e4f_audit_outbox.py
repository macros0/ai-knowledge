"""Add committed audit delivery state without replaying historical events."""
from alembic import op
from sqlalchemy import text
from app.services.audit_schema_v1 import upgrade_audit_schema, FIELDS

revision = "066b1c2d3e4f"
down_revision = "065b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    upgrade_audit_schema(op.get_bind())


def downgrade():
    if op.get_bind().execute(text("SELECT count(*) FROM audit_outbox")).scalar():
        raise RuntimeError("Nonempty audit outbox: use binary rollback without schema downgrade")
    op.drop_table("audit_outbox")
    op.drop_index("ix_audit_log_event_id",table_name="audit_log")
    for name in reversed(FIELDS):op.drop_column("audit_log",name)
