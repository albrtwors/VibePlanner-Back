"""add_participant_payments_reference

Revision ID: cb81db549fc0
Revises: 8171e68e0903
Create Date: 2026-10-23 22:10:30.968794

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cb81db549fc0'
down_revision: Union[str, Sequence[str], None] = '8171e68e0903'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('participant_payments', sa.Column('payment_reference', sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    pass
