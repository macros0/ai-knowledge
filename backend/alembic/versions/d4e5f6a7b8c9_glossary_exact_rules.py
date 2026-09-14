"""Add user-configured infotype rules and glossary write state.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("domain_terms", sa.Column("infotype_number", sa.String(length=4), nullable=True))
    op.create_index("ix_domain_terms_infotype_number", "domain_terms", ["infotype_number"])

    op.create_table(
        "glossary_infotype_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("number_from", sa.Integer(), nullable=False),
        sa.Column("number_to", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.CheckConstraint("number_from >= 0 AND number_from <= 9999", name="ck_glossary_rule_from"),
        sa.CheckConstraint("number_to >= 0 AND number_to <= 9999", name="ck_glossary_rule_to"),
        sa.CheckConstraint("number_from <= number_to", name="ck_glossary_rule_order"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "glossary_infotype_prefixes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("prefix", sa.String(length=64), nullable=False),
        sa.Column("normalized_prefix", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["rule_id"], ["glossary_infotype_rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "normalized_prefix", name="uq_glossary_rule_prefix"),
        sa.UniqueConstraint("rule_id", "position", name="uq_glossary_rule_prefix_position"),
    )
    op.create_index("ix_glossary_infotype_prefixes_rule_id", "glossary_infotype_prefixes", ["rule_id"])

    op.create_table(
        "glossary_identity_keys",
        sa.Column("key_kind", sa.String(length=32), nullable=False),
        sa.Column("key_value", sa.String(length=256), nullable=False),
        sa.Column("term_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["term_id"], ["domain_terms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("key_kind", "key_value"),
    )
    op.create_index("ix_glossary_identity_keys_term_id", "glossary_identity_keys", ["term_id"])

    op.create_table(
        "glossary_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("identities_ready", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "glossary_merge_receipts",
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("request_id"),
    )
    # Existing terms need the offline identity audit before the new namespace
    # can be trusted. Fresh databases have no legacy rows and are ready.
    op.execute(
        sa.text(
            "INSERT INTO glossary_state (id, revision, identities_ready, updated_at) "
            "SELECT 1, 0, CASE WHEN EXISTS (SELECT 1 FROM domain_terms) THEN FALSE ELSE TRUE END, CURRENT_TIMESTAMP"
        )
    )


def downgrade() -> None:
    op.drop_table("glossary_merge_receipts")
    op.drop_table("glossary_state")
    op.drop_index("ix_glossary_identity_keys_term_id", table_name="glossary_identity_keys")
    op.drop_table("glossary_identity_keys")
    op.drop_index("ix_glossary_infotype_prefixes_rule_id", table_name="glossary_infotype_prefixes")
    op.drop_table("glossary_infotype_prefixes")
    op.drop_table("glossary_infotype_rules")
    op.drop_index("ix_domain_terms_infotype_number", table_name="domain_terms")
    op.drop_column("domain_terms", "infotype_number")
