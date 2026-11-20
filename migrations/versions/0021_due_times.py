"""due time per order line and order: target time and "Delayed" (docs/14)

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa


revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Snapshot when ordered: placed + the outlet's chef pick-up time + the dish's prep time (order = slowest line).
    op.add_column('orders', sa.Column('due_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('order_items', sa.Column('due_at', sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE order_items oi SET due_at = o.created_at + make_interval(mins => "
        "COALESCE(ot.pickup_alert_minutes, 0) + oi.est_prep_minutes) "
        "FROM orders o JOIN outlets ot ON ot.id = o.outlet_id WHERE o.id = oi.order_id"
    )
    op.execute(
        "UPDATE orders o SET due_at = o.created_at + make_interval(mins => "
        "COALESCE(ot.pickup_alert_minutes, 0) + o.est_prep_minutes) FROM outlets ot WHERE ot.id = o.outlet_id"
    )


def downgrade() -> None:
    op.drop_column('order_items', 'due_at')
    op.drop_column('orders', 'due_at')
