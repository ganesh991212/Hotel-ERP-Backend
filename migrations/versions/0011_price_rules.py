"""happy hour: time-based price rules (docs/03 "Happy hour", docs/14)

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

CTX = "NULLIF(current_setting('app.hotel_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'price_rules',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('hotel_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('percent_bp', sa.Integer(), nullable=False),
        # Days of the week as bits, Monday = 1, Tuesday = 2 ... Sunday = 64 (127 = every day).
        sa.Column('days', sa.SmallInteger(), server_default='127', nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        # NULL = the whole menu.
        sa.Column('category_ids', postgresql.ARRAY(sa.UUID()), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('percent_bp BETWEEN 1 AND 9000', name='ck_price_rules_percent'),
        sa.CheckConstraint('days BETWEEN 1 AND 127', name='ck_price_rules_days'),
        sa.CheckConstraint('start_time <> end_time', name='ck_price_rules_window'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.execute("ALTER TABLE price_rules ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON price_rules USING (hotel_id = {CTX}) WITH CHECK (hotel_id = {CTX})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON price_rules TO hotel_app, hotel_platform")
    # What the dish/size cost before the rule, and which rule: reports show happy-hour sales with real amounts.
    op.add_column('order_items', sa.Column('list_unit_price_minor', sa.BigInteger(), nullable=True))
    op.add_column('order_items', sa.Column('price_rule', sa.String(length=60), nullable=True))


def downgrade() -> None:
    op.drop_column('order_items', 'price_rule')
    op.drop_column('order_items', 'list_unit_price_minor')
    op.drop_table('price_rules')
