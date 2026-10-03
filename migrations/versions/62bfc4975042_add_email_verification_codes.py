"""add email verification codes

Revision ID: 62bfc4975042
Revises: 603587739639
Create Date: 2026-10-02 12:07:37.734249

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '62bfc4975042'
down_revision: Union[str, Sequence[str], None] = '603587739639'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Tabla de códigos de un solo uso (verificación de email y reset de contraseña)
    op.create_table('verification_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('purpose', sa.String(length=20), nullable=False),
    sa.Column('code', sa.String(length=6), nullable=False),
    sa.Column('attempts_left', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_verification_codes_user_id'), 'verification_codes', ['user_id'], unique=False)

    # 1) Agregamos la columna nullable para poder hacer el backfill sobre datos existentes
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_verified', sa.Boolean(), nullable=True))

    # 2) Las cuentas que ya existían (creadas antes de este flujo) quedan verificadas
    #    para no bloquearles el acceso; las nuevas se crean explícitamente en False.
    users_table = sa.table('users', sa.column('is_verified', sa.Boolean()))
    op.execute(users_table.update().values(is_verified=sa.true()))

    # 3) Endurecemos la columna a NOT NULL
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('is_verified', existing_type=sa.Boolean(), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('is_verified')

    op.drop_index(op.f('ix_verification_codes_user_id'), table_name='verification_codes')
    op.drop_table('verification_codes')