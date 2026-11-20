"""day close / cash drawer (docs/03 "Day close", docs/14)

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa


revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None

CTX = "NULLIF(current_setting('app.hotel_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'day_closes',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('hotel_id', sa.UUID(), nullable=False),
        sa.Column('business_date', sa.Date(), nullable=False),
        # Snapshot of the server's figures at the moment of closing (integer paise).
        sa.Column('opening_float_minor', sa.BigInteger(), nullable=False),
        sa.Column('cash_sales_minor', sa.BigInteger(), nullable=False),
        sa.Column('cash_paid_out_minor', sa.BigInteger(), nullable=False),
        sa.Column('expected_minor', sa.BigInteger(), nullable=False),
        sa.Column('counted_minor', sa.BigInteger(), nullable=False),
        sa.Column('difference_minor', sa.BigInteger(), nullable=False),  # counted - expected (+ over, - short)
        sa.Column('float_kept_minor', sa.BigInteger(), nullable=False),  # stays in the drawer for tomorrow
        sa.Column('deposit_minor', sa.BigInteger(), nullable=False),  # counted - float kept
        sa.Column('upi_minor', sa.BigInteger(), nullable=False),
        sa.Column('card_minor', sa.BigInteger(), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('closed_by', sa.UUID(), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('counted_minor >= 0 AND float_kept_minor >= 0 AND float_kept_minor <= counted_minor',
                           name='ck_day_closes_amounts'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['closed_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('hotel_id', 'business_date', name='uq_day_closes_day'),
    )
    op.execute("ALTER TABLE day_closes ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON day_closes USING (hotel_id = {CTX}) WITH CHECK (hotel_id = {CTX})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON day_closes TO hotel_app, hotel_platform")
    # New permission for existing hotels' system roles (new hotels get it from SYSTEM_ROLES).
    op.execute(
        "INSERT INTO role_permissions (role_id, permission, hotel_id) "
        "SELECT id, 'dayclose.manage', hotel_id FROM roles WHERE code IN ('HOTEL_ADMIN', 'MANAGER', 'CASHIER') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission = 'dayclose.manage'")
    op.drop_table('day_closes')
