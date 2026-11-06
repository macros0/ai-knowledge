"""User-owned answer scenarios and immutable versions."""
from alembic import op
from app.services.answer_scenario_schema import upgrade_answer_scenario_schema
revision = "068b1c2d3e4f"
down_revision = "067b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    upgrade_answer_scenario_schema(op.get_bind())


def downgrade():
    op.drop_table("answer_scenario_versions")
    op.drop_table("answer_scenarios")
