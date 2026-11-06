"""Persist publication protocol identity independently of cache/binding presence."""
from alembic import op
import sqlalchemy as sa

revision = "rpl01c_portable_publication"
down_revision = "rpl01b_artifact_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_generations", sa.Column("publication_version", sa.Integer(), nullable=False, server_default="1"))
    op.execute(sa.text("""UPDATE document_generations SET publication_version=2
        WHERE EXISTS (SELECT 1 FROM artifact_bindings b WHERE b.owner_type='generation'
          AND b.owner_id=document_generations.doc_id AND b.generation_id=document_generations.id
          AND b.logical_name='publication.json')"""))


def downgrade():
    op.drop_column("document_generations", "publication_version")
