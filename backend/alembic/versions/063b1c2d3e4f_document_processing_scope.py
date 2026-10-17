"""Durable hybrid attachment review and immutable generation scope."""

from alembic import op
import sqlalchemy as sa

revision = "063b1c2d3e4f"
down_revision = "062b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("signature_scope_hash", sa.String(64), nullable=True))
    op.add_column(
        "document_staging", sa.Column("processing_scope_hash", sa.String(64), nullable=True)
    )
    with op.batch_alter_table("document_generations") as batch:
        batch.add_column(sa.Column("processing_scope_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("processing_scope_summary_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("scope_request_id", sa.String(32), nullable=True))
        batch.create_unique_constraint(
            "uq_document_generations_scope_request_id", ["scope_request_id"]
        )
    op.create_table(
        "document_processing_reviews",
        sa.Column(
            "doc_id",
            sa.String(16),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("id", sa.String(32), nullable=False, unique=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("source_file_hash", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("assessor_version", sa.String(64), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("inventory_json", sa.JSON(), nullable=False),
        sa.Column("selection_json", sa.JSON(), nullable=False),
        sa.Column("selection_hash", sa.String(64), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        *[
            sa.Column(name, sa.String(32), nullable=True)
            for name in ("base_generation_id", "generation_id", "request_id", "validation_token")
        ],
        sa.Column("validation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("similarity_confirmation_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("confirmed_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM document_processing_reviews")).scalar():
        raise RuntimeError("Processing reviews must be resolved before downgrade")
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM document_generations WHERE processing_scope_json IS NOT NULL AND CAST(processing_scope_json AS TEXT) != 'null'"
            )
        )
        .scalar()
    ):
        raise RuntimeError(
            "Processing scopes require a compatible application; cannot discard their policy"
        )
    op.drop_table("document_processing_reviews")
    with op.batch_alter_table("document_generations") as batch:
        batch.drop_constraint("uq_document_generations_scope_request_id", type_="unique")
        batch.drop_column("scope_request_id")
        batch.drop_column("processing_scope_json")
        batch.drop_column("processing_scope_summary_json")
    op.drop_column("document_staging", "processing_scope_hash")
    op.drop_column("documents", "signature_scope_hash")
