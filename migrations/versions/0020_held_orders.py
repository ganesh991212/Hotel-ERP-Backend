"""held orders: carts put on hold, resumed later (docs/03 "Hold / resume", docs/14)

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None

CTX = "NULLIF(current_setting('app.hotel_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'held_orders',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('hotel_id', sa.UUID(), nullable=False),
        sa.Column('table_id', sa.UUID(), nullable=True),  # NULL = takeaway / counter
        sa.Column('customer_name', sa.String(length=80), nullable=True),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('items', postgresql.JSONB(astext_type=sa.Text()), nullable=False),  # ids and quantities, no prices
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['table_id'], ['dining_tables.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_held_orders_hotel_id', 'held_orders', ['hotel_id'])
    op.execute("ALTER TABLE held_orders ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON held_orders USING (hotel_id = {CTX}) WITH CHECK (hotel_id = {CTX})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON held_orders TO hotel_app, hotel_platform")


def downgrade() -> None:
    op.drop_table('held_orders')
