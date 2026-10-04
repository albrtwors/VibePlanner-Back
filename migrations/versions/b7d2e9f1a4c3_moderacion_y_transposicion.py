"""moderacion y transposicion de canciones

Revision ID: b7d2e9f1a4c3
Revises: 4a9c5a4c4336
Create Date: 2026-10-03 10:00:00.000000

Agrega a 'songs' el tono y el ciclo de vida de moderación, y crea la tabla de
sugerencias del equipo.

Las canciones que ya existían se marcan como 'publicada': sin ese backfill,
al volver NOT NULL la columna quedarían como borrador y desaparecerían del
catálogo que ve todo el mundo.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2e9f1a4c3'
down_revision: Union[str, Sequence[str], None] = '4a9c5a4c4336'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('songs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('key', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column(
            'status', sa.String(length=20), nullable=False, server_default='borrador'
        ))
        batch_op.add_column(sa.Column('submitted_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('reviewed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('reviewed_by_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('review_notes', sa.String(length=400), nullable=True))
        batch_op.create_foreign_key(
            'fk_songs_reviewed_by_id_users', 'users', ['reviewed_by_id'], ['id']
        )
        batch_op.create_index(batch_op.f('ix_songs_status'), ['status'], unique=False)

    # Las canciones previas a la moderación se consideran ya publicadas.
    songs_table = sa.table(
        'songs',
        sa.column('status', sa.String(length=20)),
    )
    op.execute(songs_table.update().values(status='publicada'))

    op.create_table(
        'song_suggestions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('song_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('suggested_structure', sa.JSON(), nullable=True),
        sa.Column('suggested_key', sa.String(length=10), nullable=True),
        sa.Column('notes', sa.String(length=400), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pendiente'),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_by_id', sa.Integer(), nullable=True),
        sa.Column('resolution_notes', sa.String(length=400), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['song_id'], ['songs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['resolved_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('song_suggestions', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_song_suggestions_song_id'), ['song_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_song_suggestions_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_song_suggestions_status'), ['status'], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('song_suggestions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_song_suggestions_status'))
        batch_op.drop_index(batch_op.f('ix_song_suggestions_user_id'))
        batch_op.drop_index(batch_op.f('ix_song_suggestions_song_id'))
    op.drop_table('song_suggestions')

    with op.batch_alter_table('songs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_songs_status'))
        batch_op.drop_constraint('fk_songs_reviewed_by_id_users', type_='foreignkey')
        batch_op.drop_column('review_notes')
        batch_op.drop_column('reviewed_by_id')
        batch_op.drop_column('reviewed_at')
        batch_op.drop_column('submitted_at')
        batch_op.drop_column('status')
        batch_op.drop_column('key')
