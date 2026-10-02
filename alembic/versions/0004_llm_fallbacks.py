"""llm fallbacks

Revision ID: 7c1e4b2d9f30
Revises: 5a9a93bc0185
Create Date: 2026-10-02 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '7c1e4b2d9f30'
down_revision = '5a9a93bc0185'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user_settings', sa.Column('llm_fallbacks', postgresql.ARRAY(sa.String(length=32)),
                                             server_default='{}', nullable=False))


def downgrade() -> None:
    op.drop_column('user_settings', 'llm_fallbacks')
