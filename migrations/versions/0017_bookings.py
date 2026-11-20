"""reservations and walk-in waitlist (docs/03 "Bookings", docs/14)

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa


revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None

CTX = "NULLIF(current_setting('app.hotel_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'bookings',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('hotel_id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.String(length=8), nullable=False),  # BOOKING | WAITLIST
        sa.Column('status', sa.String(length=10), nullable=False),  # BOOKED | WAITING | SEATED | CANCELLED | NO_SHOW
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('guests', sa.SmallInteger(), nullable=False),
        sa.Column('booked_for', sa.DateTime(timezone=True), nullable=True),  # bookings only
        sa.Column('quoted_minutes', sa.SmallInteger(), nullable=True),  # waitlist only
        sa.Column('table_id', sa.UUID(), nullable=True),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('seated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("kind IN ('BOOKING', 'WAITLIST')", name='ck_bookings_kind'),
        sa.CheckConstraint("status IN ('BOOKED', 'WAITING', 'SEATED', 'CANCELLED', 'NO_SHOW')", name='ck_bookings_status'),
        sa.CheckConstraint('guests BETWEEN 1 AND 99', name='ck_bookings_guests'),
        sa.CheckConstraint("(kind = 'BOOKING') = (booked_for IS NOT NULL)", name='ck_bookings_time'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['table_id'], ['dining_tables.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_bookings_hotel_time', 'bookings', ['hotel_id', 'booked_for'])
    op.create_index('ix_bookings_hotel_phone', 'bookings', ['hotel_id', 'phone'])
    op.execute("ALTER TABLE bookings ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON bookings USING (hotel_id = {CTX}) WITH CHECK (hotel_id = {CTX})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON bookings TO hotel_app, hotel_platform")


def downgrade() -> None:
    op.drop_table('bookings')
