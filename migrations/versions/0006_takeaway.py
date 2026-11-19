"""takeaway / counter orders: customer name and phone on an order (docs/03 "Takeaway orders")

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('customer_name', sa.String(length=80), nullable=True))
    op.add_column('orders', sa.Column('customer_phone', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('orders', 'customer_phone')
    op.drop_column('orders', 'customer_name')
