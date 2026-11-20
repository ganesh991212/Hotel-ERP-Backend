"""attendance: clock in / clock out (docs/03 "Attendance", docs/14)

Revision ID: 0023
Revises: 0022
"""
from alembic import op
import sqlalchemy as sa


revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None

CTX = "NULLIF(current_setting('app.hotel_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'attendance',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('hotel_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('clock_in_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('clock_out_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('corrected_by', sa.UUID(), nullable=True),
        sa.Column('correction_reason', sa.String(length=200), nullable=True),
        sa.Column('original_in_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('original_out_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('clock_out_at IS NULL OR clock_out_at > clock_in_at', name='ck_attendance_order'),
        sa.ForeignKeyConstraint(['hotel_id'], ['hotels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['corrected_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_attendance_hotel_in', 'attendance', ['hotel_id', 'clock_in_at'])
    # At most one open shift per person.
    op.create_index(
        'uq_attendance_open', 'attendance', ['user_id'], unique=True, postgresql_where=sa.text('clock_out_at IS NULL')
    )
    op.execute("ALTER TABLE attendance ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON attendance USING (hotel_id = {CTX}) WITH CHECK (hotel_id = {CTX})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON attendance TO hotel_app, hotel_platform")
    op.execute(
        "INSERT INTO role_permissions (role_id, permission, hotel_id) "
        "SELECT id, 'attendance.manage', hotel_id FROM roles WHERE code IN ('HOTEL_ADMIN', 'MANAGER') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission = 'attendance.manage'")
    op.drop_table('attendance')
