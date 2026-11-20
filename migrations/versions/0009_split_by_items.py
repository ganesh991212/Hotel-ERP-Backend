"""split a table visit into one GST bill per guest (docs/03 "Split by items", docs/14)

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guest bills of one split share split_group_id = the first guest bill's id (the visit's orders point to it).
    op.add_column('bills', sa.Column('split_group_id', sa.UUID(), nullable=True))
    op.add_column('bills', sa.Column('guest_no', sa.SmallInteger(), nullable=True))
    op.add_column('bills', sa.Column('guest_count', sa.SmallInteger(), nullable=True))
    op.create_foreign_key('fk_bills_split_group', 'bills', 'bills', ['split_group_id'], ['id'])
    op.create_index('ix_bills_split_group', 'bills', ['split_group_id'])
    op.create_check_constraint(
        'ck_bills_guest',
        'bills',
        '(split_group_id IS NULL AND guest_no IS NULL AND guest_count IS NULL) OR '
        '(split_group_id IS NOT NULL AND guest_no BETWEEN 1 AND guest_count AND guest_count BETWEEN 2 AND 20)',
    )


def downgrade() -> None:
    op.drop_constraint('ck_bills_guest', 'bills', type_='check')
    op.drop_index('ix_bills_split_group', table_name='bills')
    op.drop_constraint('fk_bills_split_group', 'bills', type_='foreignkey')
    op.drop_column('bills', 'guest_count')
    op.drop_column('bills', 'guest_no')
    op.drop_column('bills', 'split_group_id')
