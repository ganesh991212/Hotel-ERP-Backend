"""serving hours on menu categories: breakfast / lunch / dinner (docs/03 "Serving hours", docs/14)

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa


revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('menu_categories', sa.Column('days', sa.SmallInteger(), server_default='127', nullable=False))
    op.add_column('menu_categories', sa.Column('available_from', sa.Time(), nullable=True))
    op.add_column('menu_categories', sa.Column('available_to', sa.Time(), nullable=True))
    op.create_check_constraint(
        'ck_categories_hours',
        'menu_categories',
        "days BETWEEN 1 AND 127 AND ((available_from IS NULL AND available_to IS NULL) OR "
        "(available_from IS NOT NULL AND available_to IS NOT NULL AND available_from <> available_to))",
    )


def downgrade() -> None:
    op.drop_constraint('ck_categories_hours', 'menu_categories', type_='check')
    op.drop_column('menu_categories', 'available_to')
    op.drop_column('menu_categories', 'available_from')
    op.drop_column('menu_categories', 'days')
