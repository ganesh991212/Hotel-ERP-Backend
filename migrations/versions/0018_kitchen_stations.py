"""kitchen stations: split KOTs and screens (docs/03 "Kitchen stations", docs/14)

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa


revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None

CTX = "NULLIF(current_setting('app.hotel_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'kitchen_stations',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('hotel_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=40), nullable=False),
        sa.Column('sort', sa.Integer(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('hotel_id', 'name', name='uq_kitchen_stations_name'),
    )
    op.execute("ALTER TABLE kitchen_stations ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON kitchen_stations USING (hotel_id = {CTX}) WITH CHECK (hotel_id = {CTX})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON kitchen_stations TO hotel_app, hotel_platform")
    # A category sets the station; a dish may override it. Deleting a station sends its dishes to the main kitchen.
    for t in ('menu_categories', 'menu_items'):
        op.add_column(t, sa.Column('station_id', sa.UUID(), nullable=True))
        op.create_foreign_key(f'fk_{t}_station', t, 'kitchen_stations', ['station_id'], ['id'], ondelete='SET NULL')
    # Where the dish was sent when ordered (snapshot; menu changes never move a ticket).
    op.add_column('order_items', sa.Column('station_id', sa.UUID(), nullable=True))
    op.add_column('order_items', sa.Column('station_name', sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column('order_items', 'station_name')
    op.drop_column('order_items', 'station_id')
    for t in ('menu_items', 'menu_categories'):
        op.drop_constraint(f'fk_{t}_station', t, type_='foreignkey')
        op.drop_column(t, 'station_id')
    op.drop_table('kitchen_stations')
