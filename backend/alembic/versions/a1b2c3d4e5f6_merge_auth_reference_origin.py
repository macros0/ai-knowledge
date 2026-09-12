"""Merge auth and reference-origin migration branches.

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6, f8d9e0a1b2c3
Create Date: 2026-09-11
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = ("f1a2b3c4d5e6", "f8d9e0a1b2c3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Converge the existing migration branches without changing schema."""
    pass


def downgrade() -> None:
    """Split back to the two published parent revisions."""
    pass
