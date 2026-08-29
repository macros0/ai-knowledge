"""stage 2a: audit_log, jobs, user_blocks

Revision ID: b7f2c9d3e4a1
Revises: aa65b69e08bd
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7f2c9d3e4a1'
down_revision: Union[str, Sequence[str], None] = 'aa65b69e08bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('audit_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_id', sa.String(length=255), nullable=True),
    sa.Column('username', sa.String(length=255), nullable=True),
    sa.Column('action_type', sa.String(length=64), nullable=False),
    sa.Column('target_type', sa.String(length=64), nullable=True),
    sa.Column('target_id', sa.String(length=255), nullable=True),
    sa.Column('old_value', sa.JSON(), nullable=True),
    sa.Column('new_value', sa.JSON(), nullable=True),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('meta', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_log_created_at'), 'audit_log', ['created_at'], unique=False)
    op.create_index(op.f('ix_audit_log_action_type'), 'audit_log', ['action_type'], unique=False)
    op.create_index(op.f('ix_audit_log_target_id'), 'audit_log', ['target_id'], unique=False)

    op.create_table('jobs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('job_type', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('created_by_id', sa.String(length=255), nullable=True),
    sa.Column('created_by', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approved_by', sa.String(length=255), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('params', sa.JSON(), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_jobs_status'), 'jobs', ['status'], unique=False)

    op.create_table('user_blocks',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('username', sa.String(length=255), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('blocked_by', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_blocks_external_id'), 'user_blocks', ['external_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_user_blocks_external_id'), table_name='user_blocks')
    op.drop_table('user_blocks')
    op.drop_index(op.f('ix_jobs_status'), table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_audit_log_target_id'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_action_type'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_created_at'), table_name='audit_log')
    op.drop_table('audit_log')
