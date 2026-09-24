"""Merge glossary and document error-code migration heads."""

from typing import Sequence, Union


revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, Sequence[str], None] = ("b2c3d4e5f6a7", "c7d8e9f0a1b2")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
