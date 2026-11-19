"""indexes for date-filtered orders and bills lists (docs/06 "Lists by date")

Revision ID: 0005
Revises: 0004
"""
from alembic import op


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_orders_hotel_created', 'orders', ['hotel_id', 'created_at'])
    op.create_index('ix_bills_hotel_created', 'bills', ['hotel_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_bills_hotel_created', table_name='bills')
    op.drop_index('ix_orders_hotel_created', table_name='orders')
