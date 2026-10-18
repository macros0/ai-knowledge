"""Durable reference revisions and expiring editor leases; no document reindex."""
import json

from alembic import op
import sqlalchemy as sa

revision = "065b1c2d3e4f"
down_revision = "064b1c2d3e4f"
branch_labels = None
depends_on = None


def _key(kind, identifier, subkind=None, org_id=None):
    return json.dumps([kind, str(identifier), subkind, org_id], separators=(",", ":"))


def upgrade():
    connection = op.get_bind()
    names = set(sa.inspect(connection).get_table_names())
    if "attribute_values" in names:
        duplicates = connection.execute(sa.text("SELECT attribute_key, value, count(*) AS count "
            "FROM attribute_values WHERE org_id IS NULL GROUP BY attribute_key, value HAVING count(*) > 1")).mappings().all()
        if duplicates:
            raise RuntimeError("Duplicate global attributes require manual resolution before migration: " +
                json.dumps([dict(row) for row in duplicates], ensure_ascii=False))
        if "uq_attribute_values_global" not in {index["name"] for index in sa.inspect(connection).get_indexes("attribute_values")}:
            op.create_index("uq_attribute_values_global", "attribute_values", ["attribute_key", "value"], unique=True,
                postgresql_where=sa.text("org_id IS NULL"), sqlite_where=sa.text("org_id IS NULL"))
    op.create_table("reference_resource_states",
        sa.Column("resource_key", sa.String(255), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("owner_id", sa.String(255), nullable=True),
        sa.Column("owner_name", sa.String(255), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activity_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("revision >= 1 AND lease_epoch >= 0 AND activity_sequence >= 0", name="ck_reference_state_bounds"))
    op.create_index("ix_reference_resource_states_expires_at", "reference_resource_states", ["expires_at"])
    keys = set()
    for table, kind in [("domain_terms", "glossary_term"), ("glossary_infotype_rules", "glossary_rule"),
                        ("developments", "development"), ("tags", "tag")]:
        if table in names:
            keys.update(_key(kind, item[0]) for item in connection.execute(sa.text(f"SELECT id FROM {table}")))
    if "locales" in names:
        for (locale,) in connection.execute(sa.text("SELECT code FROM locales")):
            keys.add(_key("locale", locale))
            keys.add(_key("ui_dictionary", locale))
            keys.update(_key("stopword_set", locale, kind) for kind in ["bm25", "marker"])
    if "attribute_values" in names:
        for attribute, org in connection.execute(sa.text("SELECT DISTINCT attribute_key, org_id FROM attribute_values")):
            keys.add(_key("attribute_catalog", attribute, org_id=org))
    table = sa.table("reference_resource_states", sa.column("resource_key", sa.String()))
    ordered = sorted(keys)
    for offset in range(0, len(ordered), 500):
        op.bulk_insert(table, [{"resource_key": key} for key in ordered[offset:offset + 500]])


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM reference_resource_states WHERE revision > 1 OR owner_id IS NOT NULL")).scalar():
        raise RuntimeError("Reference editing state is in use; keep the additive schema during rollback")
    op.drop_index("ix_reference_resource_states_expires_at", table_name="reference_resource_states")
    op.drop_table("reference_resource_states")
    if "attribute_values" in sa.inspect(bind).get_table_names():
        op.drop_index("uq_attribute_values_global", table_name="attribute_values")
