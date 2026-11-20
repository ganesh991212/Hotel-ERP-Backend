"""shift rota: planned shifts per person (docs/03 "Shift rota", docs/14)

Revision ID: 0024
Revises: 0023
"""
from alembic import op
import sqlalchemy as sa


revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None

CTX = "NULLIF(current_setting('app.hotel_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'shift_plans',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('hotel_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),  # hotel-local day the shift starts
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('note', sa.String(length=80), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.CheckConstraint('ends_at > starts_at', name='ck_shift_plans_order'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_shift_plans_hotel_day', 'shift_plans', ['hotel_id', 'day'])
    op.execute("ALTER TABLE shift_plans ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON shift_plans USING (hotel_id = {CTX}) WITH CHECK (hotel_id = {CTX})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON shift_plans TO hotel_app, hotel_platform")


def downgrade() -> None:
    op.drop_table('shift_plans')
