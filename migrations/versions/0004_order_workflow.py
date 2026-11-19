"""order workflow: serving mode, no-pickup alert (docs/04 "Order workflow")

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('outlets', sa.Column('serve_mode', sa.String(length=8), server_default='SERVER', nullable=False))
    op.add_column('outlets', sa.Column('pickup_alert_minutes', sa.Integer(), server_default='5', nullable=True))
    op.create_check_constraint('ck_outlets_serve_mode', 'outlets', "serve_mode IN ('SERVER', 'COUNTER')")
    op.create_check_constraint(
        'ck_outlets_pickup_alert_minutes', 'outlets', 'pickup_alert_minutes IS NULL OR pickup_alert_minutes BETWEEN 1 AND 60'
    )
    op.add_column('orders', sa.Column('pickup_alerted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('orders', 'pickup_alerted_at')
    op.drop_constraint('ck_outlets_pickup_alert_minutes', 'outlets', type_='check')
    op.drop_constraint('ck_outlets_serve_mode', 'outlets', type_='check')
    op.drop_column('outlets', 'pickup_alert_minutes')
    op.drop_column('outlets', 'serve_mode')
