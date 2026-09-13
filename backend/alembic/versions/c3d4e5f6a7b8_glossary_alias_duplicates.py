"""Allow shared aliases across terms; keep each term's aliases unique.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None

TABLE = "domain_term_aliases"
PER_TERM = "uq_domain_term_alias_per_term"
INDEX = "ix_domain_term_aliases_normalized_alias"
NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade():
    inspector = sa.inspect(op.get_bind())
    constraints = inspector.get_unique_constraints(TABLE)
    global_unique = [c for c in constraints if c["column_names"] == ["normalized_alias"]]
    has_per_term = any(c["column_names"] == ["term_id", "normalized_alias"] for c in constraints)
    if global_unique or not has_per_term:
        with op.batch_alter_table(TABLE, naming_convention=NAMING) as batch:
            for constraint in global_unique:
                batch.drop_constraint(constraint["name"] or "uq_domain_term_aliases_normalized_alias", type_="unique")
            if not has_per_term:
                batch.create_unique_constraint(PER_TERM, ["term_id", "normalized_alias"])
    if INDEX not in {item["name"] for item in inspector.get_indexes(TABLE)}:
        op.create_index(INDEX, TABLE, ["normalized_alias"])


def downgrade():
    duplicate = op.get_bind().execute(sa.text(
        "SELECT normalized_alias FROM domain_term_aliases "
        "GROUP BY normalized_alias HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicate:
        raise RuntimeError("Resolve duplicate glossary aliases before downgrading; no data was removed")
    with op.batch_alter_table(TABLE, naming_convention=NAMING) as batch:
        batch.drop_constraint(PER_TERM, type_="unique")
        batch.create_unique_constraint("uq_domain_term_aliases_normalized_alias", ["normalized_alias"])
    op.drop_index(INDEX, table_name=TABLE)
