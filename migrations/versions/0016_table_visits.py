"""tables: guests, time seated, Reserved / Cleaning; covers on bills (docs/03 "Table visits", docs/14)

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa


revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('dining_tables', sa.Column('seated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('dining_tables', sa.Column('guests', sa.SmallInteger(), nullable=True))
    op.add_column('dining_tables', sa.Column('hold_state', sa.String(length=10), nullable=True))
    op.add_column('dining_tables', sa.Column('hold_note', sa.String(length=80), nullable=True))
    op.create_check_constraint(
        'ck_tables_visit',
        'dining_tables',
        "(hold_state IS NULL OR hold_state IN ('RESERVED', 'CLEANING')) AND (guests IS NULL OR guests BETWEEN 1 AND 99)",
    )
    # Covers: the visit's guest count, kept on its bill (on the first guest bill only when split).
    op.add_column('bills', sa.Column('guests', sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('bills', 'guests')
    op.drop_constraint('ck_tables_visit', 'dining_tables', type_='check')
    for c in ('hold_note', 'hold_state', 'guests', 'seated_at'):
        op.drop_column('dining_tables', c)
