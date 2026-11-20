"""order items keep the order they were entered in (KOT, order screens)

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('order_items', sa.Column('line_no', sa.SmallInteger(), server_default='0', nullable=False))


def downgrade() -> None:
    op.drop_column('order_items', 'line_no')
