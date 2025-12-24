"""Add billing enrollment fields

Revision ID: d1e2f3a4b5c6
Revises: c4a3b2d1e0f
Create Date: 2025-12-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c4a3b2d1e0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add billing enrollment fields to user table
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email_hmac', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('billing_customer_id', sa.String(length=255), nullable=True))
        batch_op.create_index(batch_op.f('ix_user_email_hmac'), ['email_hmac'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_billing_customer_id'), ['billing_customer_id'], unique=True)

    # Add email_hmac to auth table
    with op.batch_alter_table('auth', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email_hmac', sa.String(length=255), nullable=True))


def downgrade() -> None:
    # Remove billing enrollment fields
    with op.batch_alter_table('auth', schema=None) as batch_op:
        batch_op.drop_column('email_hmac')

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_billing_customer_id'))
        batch_op.drop_index(batch_op.f('ix_user_email_hmac'))
        batch_op.drop_column('billing_customer_id')
        batch_op.drop_column('email_hmac')
