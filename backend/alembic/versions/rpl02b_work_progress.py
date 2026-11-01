"""Persist bounded, fenced work cursors independently of immutable inputs."""
from alembic import op
import sqlalchemy as sa

revision = "rpl02b_work_progress"
down_revision = "rpl02a_document_work"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("work_progress",
        sa.Column("work_id", sa.String(32), sa.ForeignKey("work_items.id"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_work_progress_version"))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM work_progress")):
        raise RuntimeError("Accepted work progress requires coherent recovery")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM work_items WHERE kind='scope_change'")):
        raise RuntimeError("Accepted scope work requires coherent recovery")
    op.drop_table("work_progress")
