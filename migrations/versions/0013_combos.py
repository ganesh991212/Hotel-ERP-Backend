"""combos: a menu item made of other items (docs/03 "Combos", docs/14)

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None

CTX = "NULLIF(current_setting('app.hotel_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'menu_item_components',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('hotel_id', sa.UUID(), nullable=False),
        sa.Column('combo_item_id', sa.UUID(), nullable=False),
        sa.Column('component_item_id', sa.UUID(), nullable=False),
        sa.Column('qty', sa.Integer(), server_default='1', nullable=False),
        sa.Column('sort', sa.Integer(), server_default='0', nullable=False),
        sa.CheckConstraint('qty BETWEEN 1 AND 20', name='ck_components_qty'),
        sa.CheckConstraint('combo_item_id <> component_item_id', name='ck_components_not_self'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['combo_item_id'], ['menu_items.id'], ondelete='CASCADE'),
        # A dish used in a combo can't be deleted out from under it (mark it sold out instead).
        sa.ForeignKeyConstraint(['component_item_id'], ['menu_items.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('combo_item_id', 'component_item_id', name='uq_combo_component'),
    )
    op.create_index('ix_menu_item_components_combo', 'menu_item_components', ['combo_item_id'])
    op.create_index('ix_menu_item_components_component', 'menu_item_components', ['component_item_id'])
    op.execute("ALTER TABLE menu_item_components ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON menu_item_components USING (hotel_id = {CTX}) WITH CHECK (hotel_id = {CTX})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON menu_item_components TO hotel_app, hotel_platform")
    # Snapshot of what the combo contained when ordered: [{"item_id", "name", "qty"}] (KOT, later inventory).
    op.add_column('order_items', sa.Column('components', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('order_items', 'components')
    op.drop_table('menu_item_components')
