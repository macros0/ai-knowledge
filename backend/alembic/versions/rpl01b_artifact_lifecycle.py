"""Read leases and durable deletion claims/tombstones."""
from alembic import op
import sqlalchemy as sa

revision = "rpl01b_artifact_lifecycle"
down_revision = "rpl01_artifact_objects"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("artifact_objects") as batch:
        batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), server_default=sa.func.current_timestamp())
        batch.add_column(sa.Column("state", sa.String(16), nullable=False, server_default="ready"))
        batch.add_column(sa.Column("delete_token", sa.String(32), nullable=True))
        batch.add_column(sa.Column("delete_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint("ck_artifact_state", "state IN ('ready', 'deleting', 'deleted')")
    op.create_table("artifact_read_leases",
        sa.Column("object_id", sa.String(32), sa.ForeignKey("artifact_objects.object_id"), primary_key=True),
        sa.Column("owner_token", sa.String(32), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_artifact_read_leases_expires_at", "artifact_read_leases", ["expires_at"])
    op.create_table("artifact_checkpoints",
        sa.Column("doc_id", sa.String(16), sa.ForeignKey("documents.id"), primary_key=True),
        sa.Column("generation_id", sa.String(32), sa.ForeignKey("document_generations.id"), primary_key=True),
        sa.Column("chunk_index", sa.Integer(), primary_key=True),
        sa.Column("source_signature", sa.String(64), primary_key=True),
        sa.Column("object_id", sa.String(32), sa.ForeignKey("artifact_objects.object_id"), nullable=False),
        sa.CheckConstraint("chunk_index >= 0", name="ck_artifact_checkpoint_index"))
    op.create_index("ix_artifact_checkpoints_object_id", "artifact_checkpoints", ["object_id"])


def downgrade():
    op.drop_table("artifact_checkpoints")
    op.drop_table("artifact_read_leases")
    with op.batch_alter_table("artifact_objects") as batch:
        batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), server_default=None)
        batch.drop_constraint("ck_artifact_state", type_="check")
        batch.drop_column("delete_expires_at")
        batch.drop_column("delete_token")
        batch.drop_column("state")
