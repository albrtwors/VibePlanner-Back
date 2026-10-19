"""add_contact_fields_to_participants

Revision ID: 58a5aaa74d8b
Revises: b7d2e9f1a4c3
Create Date: 2026-10-19 04:51:10.814348

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58a5aaa74d8b'
down_revision: Union[str, Sequence[str], None] = 'b7d2e9f1a4c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('participants', schema=None) as batch_op:
        batch_op.add_column(sa.Column('contact_type', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('contact_value', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('contact_phone', sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('participants', schema=None) as batch_op:
        batch_op.drop_column('contact_phone')
        batch_op.drop_column('contact_value')
        batch_op.drop_column('contact_type')
