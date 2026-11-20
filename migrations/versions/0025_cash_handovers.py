"""cash handover between shifts: a blind count per shift (docs/03 "Cash handover", docs/14)

Revision ID: 0025
Revises: 0024
"""
from alembic import op
import sqlalchemy as sa


revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None

CTX = "NULLIF(current_setting('app.hotel_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'cash_handovers',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('hotel_id', sa.UUID(), nullable=False),
        sa.Column('business_date', sa.Date(), nullable=False),
        sa.Column('since_at', sa.DateTime(timezone=True), nullable=True),  # previous handover, or the day's start
        sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('opening_minor', sa.BigInteger(), nullable=False),
        sa.Column('cash_in_minor', sa.BigInteger(), nullable=False),
        sa.Column('paid_out_minor', sa.BigInteger(), nullable=False),
        sa.Column('expected_minor', sa.BigInteger(), nullable=False),
        sa.Column('counted_minor', sa.BigInteger(), nullable=False),
        sa.Column('difference_minor', sa.BigInteger(), nullable=False),
        sa.Column('upi_minor', sa.BigInteger(), nullable=False),
        sa.Column('card_minor', sa.BigInteger(), nullable=False),
        sa.Column('handed_by', sa.UUID(), nullable=True),
        sa.Column('taken_by', sa.UUID(), nullable=True),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['handed_by'], ['users.id']),
        sa.ForeignKeyConstraint(['taken_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cash_handovers_hotel_day', 'cash_handovers', ['hotel_id', 'business_date'])
    op.execute("ALTER TABLE cash_handovers ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON cash_handovers USING (hotel_id = {CTX}) WITH CHECK (hotel_id = {CTX})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON cash_handovers TO hotel_app, hotel_platform")


def downgrade() -> None:
    op.drop_table('cash_handovers')
