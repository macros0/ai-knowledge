"""Preserve full canonical section titles for generic fragment coverage."""

from alembic import op
import sqlalchemy as sa

revision = "062b1c2d3e4f"
down_revision = "061b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("document_chunks") as batch:
        batch.alter_column(
            "section_title", existing_type=sa.String(1024), type_=sa.Text(), existing_nullable=True
        )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT COUNT(*) FROM document_chunks WHERE length(section_title) > 1024"))
        .scalar()
    ):
        raise RuntimeError("Cannot narrow canonical chunk titles without data loss")
    with op.batch_alter_table("document_chunks") as batch:
        batch.alter_column(
            "section_title", existing_type=sa.Text(), type_=sa.String(1024), existing_nullable=True
        )
