"""Add billing enrollment fields

Revision ID: d1e2f3a4b5c6
Revises: c4a3b2d1e0f
Create Date: 2025-12-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c4a3b2d1e0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # Check existing columns in user table
    user_columns = set()
    if "user" in existing_tables:
        user_columns = {col["name"] for col in inspector.get_columns("user")}

    # Check existing indexes in user table
    user_indexes = set()
    if "user" in existing_tables:
        user_indexes = {idx["name"] for idx in inspector.get_indexes("user")}

    # Add billing enrollment fields to user table (idempotent)
    with op.batch_alter_table('user', schema=None) as batch_op:
        if 'email_hmac' not in user_columns:
            batch_op.add_column(sa.Column('email_hmac', sa.String(length=255), nullable=True))
        if 'billing_customer_id' not in user_columns:
            batch_op.add_column(sa.Column('billing_customer_id', sa.String(length=255), nullable=True))
        if 'ix_user_email_hmac' not in user_indexes:
            batch_op.create_index(batch_op.f('ix_user_email_hmac'), ['email_hmac'], unique=False)
        if 'ix_user_billing_customer_id' not in user_indexes:
            batch_op.create_index(batch_op.f('ix_user_billing_customer_id'), ['billing_customer_id'], unique=True)

    # Check existing columns in auth table
    auth_columns = set()
    if "auth" in existing_tables:
        auth_columns = {col["name"] for col in inspector.get_columns("auth")}

    # Add email_hmac to auth table (idempotent)
    with op.batch_alter_table('auth', schema=None) as batch_op:
        if 'email_hmac' not in auth_columns:
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
