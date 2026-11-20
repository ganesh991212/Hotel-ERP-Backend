"""category on order and bill lines, for category-wise sales (docs/14 "Category-wise sales")

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa


revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Snapshot like the name and price: a later menu change never moves past sales to another category.
    op.add_column('order_items', sa.Column('category_name', sa.String(length=80), nullable=True))
    op.add_column('bill_lines', sa.Column('category_name', sa.String(length=80), nullable=True))
    # Order lines already placed: their dish's category now (orders are not tax documents and may still be open).
    op.execute(
        "UPDATE order_items oi SET category_name = c.name FROM menu_items m JOIN menu_categories c "
        "ON c.id = m.category_id WHERE m.id = oi.item_id"
    )
    # Issued bill lines: where the printed line name is exactly a dish's name on that bill's orders (a line with
    # add-ons or a happy-hour tag has a longer name and is left alone). The rest stay NULL and show as
    # "Not recorded", never guessed from today's menu.
    op.execute(
        "UPDATE bill_lines bl SET category_name = x.category_name FROM ("
        " SELECT DISTINCT ON (o.bill_id, oi.name) o.bill_id, oi.name, oi.category_name"
        " FROM order_items oi JOIN orders o ON o.id = oi.order_id WHERE o.bill_id IS NOT NULL"
        " ORDER BY o.bill_id, oi.name, o.created_at, oi.line_no"
        ") x WHERE x.bill_id = bl.bill_id AND x.name = bl.name"
    )

def downgrade() -> None:
    op.drop_column('bill_lines', 'category_name')
    op.drop_column('order_items', 'category_name')
