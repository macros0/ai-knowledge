"""Keep durable staging protocol identity independent of checkpoint bindings."""
from alembic import op
import sqlalchemy as sa

revision = "rpl01d_portable_staging"
down_revision = "rpl01c_portable_publication"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_staging", sa.Column("checkpoint_version", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM document_staging WHERE checkpoint_version=2")):
        raise RuntimeError("Cannot drop portable staging protocol marker; finish or explicitly migrate checkpoints first")
    op.drop_column("document_staging", "checkpoint_version")
