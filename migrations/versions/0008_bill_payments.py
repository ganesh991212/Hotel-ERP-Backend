"""part payments on a bill (docs/03 "Payments")

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'bill_payments',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('hotel_id', sa.UUID(), nullable=False),
        sa.Column('bill_id', sa.UUID(), nullable=False),
        sa.Column('method', sa.String(length=8), nullable=False),
        sa.Column('amount_minor', sa.BigInteger(), nullable=False),
        sa.Column('reference', sa.String(length=60), nullable=True),
        sa.Column('received_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("method IN ('CASH', 'UPI', 'CARD')", name='ck_bill_payments_method'),
        sa.CheckConstraint('amount_minor > 0', name='ck_bill_payments_amount'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['received_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_bill_payments_bill_id', 'bill_payments', ['bill_id'])
    op.create_index('ix_bill_payments_hotel_created', 'bill_payments', ['hotel_id', 'created_at'])
    ctx = "NULLIF(current_setting('app.hotel_id', true), '')::uuid"
    op.execute("ALTER TABLE bill_payments ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON bill_payments USING (hotel_id = {ctx}) WITH CHECK (hotel_id = {ctx})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON bill_payments TO hotel_app, hotel_platform")
    # Bills paid before part payments existed: one payment each, so takings by method stay complete.
    op.execute(
        "INSERT INTO bill_payments (hotel_id, bill_id, method, amount_minor, received_by, created_at) "
        "SELECT hotel_id, id, payment_method, total_minor, created_by, COALESCE(paid_at, created_at) "
        "FROM bills WHERE status = 'PAID' AND payment_method IN ('CASH', 'UPI', 'CARD') AND total_minor > 0"
    )


def downgrade() -> None:
    op.drop_table('bill_payments')
