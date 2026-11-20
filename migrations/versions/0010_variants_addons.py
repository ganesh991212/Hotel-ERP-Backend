"""menu variants (half/full) and add-on groups (docs/03 "Variants and add-ons", docs/14)

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None

CTX = "NULLIF(current_setting('app.hotel_id', true), '')::uuid"
NEW = ('menu_item_variants', 'addon_groups', 'addons', 'menu_item_addon_groups')


def _common():
    return [
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('hotel_id', sa.UUID(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        'menu_item_variants',
        *_common(),
        sa.Column('item_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=40), nullable=False),
        sa.Column('price_minor', sa.BigInteger(), nullable=False),
        sa.Column('is_available', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('sort', sa.Integer(), server_default='0', nullable=False),
        sa.CheckConstraint('price_minor >= 0', name='ck_variants_price'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['item_id'], ['menu_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('item_id', 'name', name='uq_variants_item_name'),
    )
    op.create_index('ix_menu_item_variants_item_id', 'menu_item_variants', ['item_id'])
    op.create_table(
        'addon_groups',
        *_common(),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('min_select', sa.Integer(), server_default='0', nullable=False),
        sa.Column('max_select', sa.Integer(), server_default='1', nullable=False),
        sa.Column('sort', sa.Integer(), server_default='0', nullable=False),
        sa.CheckConstraint('min_select >= 0 AND max_select >= 1 AND min_select <= max_select AND max_select <= 20',
                           name='ck_addon_groups_range'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'addons',
        *_common(),
        sa.Column('group_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('price_minor', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('is_available', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('sort', sa.Integer(), server_default='0', nullable=False),
        sa.CheckConstraint('price_minor >= 0', name='ck_addons_price'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['group_id'], ['addon_groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_addons_group_id', 'addons', ['group_id'])
    op.create_table(
        'menu_item_addon_groups',
        *_common(),
        sa.Column('item_id', sa.UUID(), nullable=False),
        sa.Column('group_id', sa.UUID(), nullable=False),
        sa.Column('sort', sa.Integer(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['item_id'], ['menu_items.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['group_id'], ['addon_groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('item_id', 'group_id', name='uq_item_addon_group'),
    )
    op.create_index('ix_menu_item_addon_groups_group_id', 'menu_item_addon_groups', ['group_id'])
    for t in NEW:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING (hotel_id = {CTX}) WITH CHECK (hotel_id = {CTX})")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO hotel_app, hotel_platform")
    # Snapshot on the order line: what was chosen, at the price of the moment (menu edits never change an order).
    op.add_column('order_items', sa.Column('variant_id', sa.UUID(), nullable=True))
    op.add_column('order_items', sa.Column('variant_name', sa.String(length=40), nullable=True))
    op.add_column('order_items', sa.Column('addons', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('order_items', 'addons')
    op.drop_column('order_items', 'variant_name')
    op.drop_column('order_items', 'variant_id')
    for t in reversed(NEW):
        op.drop_table(t)
