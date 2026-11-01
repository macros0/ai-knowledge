"""Fence replica writers across maintenance; existing installations start held."""
from alembic import op
import sqlalchemy as sa

revision = "rpl05_maintenance_epoch"
down_revision = "rpl04_runtime_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("work_items", sa.Column("maintenance_epoch", sa.BigInteger(), nullable=False, server_default="0"))
    table = op.create_table("replica_maintenance_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("installation_id", sa.String(32), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("operation_id", sa.String(32), nullable=True),
        sa.Column("operation_kind", sa.String(16), nullable=True),
        sa.Column("owner_id", sa.String(128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("intended_counts_json", sa.JSON(), nullable=False),
        sa.Column("writer_receipts_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_replica_maintenance_singleton"),
        sa.CheckConstraint("epoch >= 0", name="ck_replica_maintenance_epoch"))
    connection = op.get_bind()
    installed = connection.execute(sa.text("SELECT installation_id FROM replica_runtime_identity")).scalars().all()
    if installed:
        if len(installed) != 1:
            raise RuntimeError("Replica installation identity is ambiguous")
        controls = connection.execute(sa.text("SELECT id,installation_id,maintenance_token FROM artifact_storage_control")).mappings().all()
        if (len(controls) != 1 or controls[0]["installation_id"] != installed[0] or controls[0]["maintenance_token"]):
            raise RuntimeError("Storage operation must finish before replica schema upgrade")
        connection.execute(table.insert().values(id=1, installation_id=installed[0], epoch=1,
            enabled=True, operation_id="0" * 32, operation_kind="upgrade", owner_id="schema-migration",
            started_at=sa.func.current_timestamp(), intended_counts_json={}, writer_receipts_json={}))
        # Old binaries check their exact installed schema at every guarded SQL
        # boundary; retire them in this same migration transaction.
        connection.execute(sa.text("UPDATE replica_runtime_identity SET schema_revision=:revision"), {"revision": revision})
        connection.execute(sa.text("UPDATE artifact_storage_control SET maintenance_token=:operation"), {"operation": "0" * 32})


def downgrade():
    connection = op.get_bind()
    if (connection.scalar(sa.text("SELECT count(*) FROM replica_runtime_identity"))
            or connection.scalar(sa.text("SELECT count(*) FROM replica_maintenance_state WHERE epoch <> 0 OR enabled"))
            or connection.scalar(sa.text("SELECT count(*) FROM work_items WHERE maintenance_epoch <> 0"))):
        raise RuntimeError("Maintenance history requires coherent recovery")
    op.drop_table("replica_maintenance_state")
    op.drop_column("work_items", "maintenance_epoch")
