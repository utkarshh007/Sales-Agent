"""two-factor authentication

Revision ID: d8f6fb57926e
Revises: 6d311f828c8c
Create Date: 2026-10-09 02:10:55.034250

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8f6fb57926e'
down_revision: Union[str, Sequence[str], None] = '6d311f828c8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('session_version', sa.Integer(), server_default='0', nullable=False))
    op.add_column('users', sa.Column('totp_secret', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('totp_enabled', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column('users', sa.Column('totp_last_step', sa.BigInteger(), nullable=True))
    op.add_column('users', sa.Column('totp_enabled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('recovery_codes', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'recovery_codes')
    op.drop_column('users', 'totp_enabled_at')
    op.drop_column('users', 'totp_last_step')
    op.drop_column('users', 'totp_enabled')
    op.drop_column('users', 'totp_secret')
    op.drop_column('users', 'session_version')
