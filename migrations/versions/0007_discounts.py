"""bill discounts with a per-role maximum (docs/03 "Discounts")

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('bills', sa.Column('discount_minor', sa.BigInteger(), server_default='0', nullable=False))
    op.add_column('bills', sa.Column('discount_reason', sa.String(length=200), nullable=True))
    op.add_column('bills', sa.Column('discount_by', sa.UUID(), sa.ForeignKey('users.id'), nullable=True))
    op.add_column('bill_lines', sa.Column('discount_minor', sa.BigInteger(), server_default='0', nullable=False))
    op.add_column('roles', sa.Column('max_discount_bp', sa.Integer(), server_default='0', nullable=False))
    op.create_check_constraint('ck_roles_max_discount_bp', 'roles', 'max_discount_bp BETWEEN 0 AND 10000')
    # Starting limits for existing hotels' system roles; each hotel admin can change them (Team > Roles).
    op.execute("UPDATE roles SET max_discount_bp = 10000 WHERE code = 'HOTEL_ADMIN'")
    op.execute("UPDATE roles SET max_discount_bp = 2000 WHERE code = 'MANAGER'")
    op.execute("UPDATE roles SET max_discount_bp = 1000 WHERE code = 'CASHIER'")


def downgrade() -> None:
    op.drop_constraint('ck_roles_max_discount_bp', 'roles', type_='check')
    op.drop_column('roles', 'max_discount_bp')
    op.drop_column('bill_lines', 'discount_minor')
    op.drop_column('bills', 'discount_by')
    op.drop_column('bills', 'discount_reason')
    op.drop_column('bills', 'discount_minor')
