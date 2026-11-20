"""B2B bills: the buyer's name, GSTIN and address (docs/03 "B2B invoices", docs/14)

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa


revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('bills', sa.Column('buyer_name', sa.String(length=120), nullable=True))
    op.add_column('bills', sa.Column('buyer_gstin', sa.String(length=15), nullable=True))
    op.add_column('bills', sa.Column('buyer_address', sa.String(length=300), nullable=True))
    op.create_check_constraint('ck_bills_buyer', 'bills', '(buyer_gstin IS NULL) = (buyer_name IS NULL)')


def downgrade() -> None:
    op.drop_constraint('ck_bills_buyer', 'bills', type_='check')
    op.drop_column('bills', 'buyer_address')
    op.drop_column('bills', 'buyer_gstin')
    op.drop_column('bills', 'buyer_name')
