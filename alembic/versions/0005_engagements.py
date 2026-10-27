"""reading engagement

Revision ID: 9d4f2a6c1b85
Revises: 7c1e4b2d9f30
Create Date: 2026-10-27 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '9d4f2a6c1b85'
down_revision = '7c1e4b2d9f30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user_settings', sa.Column('track_reading', sa.Boolean(), server_default='true', nullable=False))
    op.add_column('user_settings', sa.Column('signals_since_refine', sa.Integer(), server_default='0',
                                             nullable=False))
    op.create_table(
        'engagements',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('opens', sa.Integer(), nullable=False),
        sa.Column('read_seconds', sa.Integer(), nullable=False),
        sa.Column('max_scroll', sa.Integer(), nullable=False),
        sa.Column('original_opens', sa.Integer(), nullable=False),
        sa.Column('link_clicks', sa.Integer(), nullable=False),
        sa.Column('comments_seen', sa.Boolean(), nullable=False),
        sa.Column('counted', sa.Boolean(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'item_id'),
    )
    op.create_index('ix_engagements_user_seen', 'engagements', ['user_id', 'last_seen_at'])


def downgrade() -> None:
    op.drop_index('ix_engagements_user_seen', table_name='engagements')
    op.drop_table('engagements')
    op.drop_column('user_settings', 'signals_since_refine')
    op.drop_column('user_settings', 'track_reading')
