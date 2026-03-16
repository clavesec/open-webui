"""Add encryption fields to user table

Revision ID: c4a3b2d1e0f
Revises: 9f0c9cd09105
Create Date: 2025-07-09 14:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "c4a3b2d1e0f"
down_revision = "9f0c9cd09105"  # The last stable migration
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # Check existing columns in user table
    user_columns = set()
    if "user" in existing_tables:
        user_columns = {col["name"] for col in inspector.get_columns("user")}

    # Add columns only if they don't exist (idempotent)
    with op.batch_alter_table("user", schema=None) as batch_op:
        if "salt" not in user_columns:
            batch_op.add_column(sa.Column("salt", sa.LargeBinary(), nullable=True))
        if "user_encrypted_dek" not in user_columns:
            batch_op.add_column(
                sa.Column("user_encrypted_dek", sa.LargeBinary(), nullable=True)
            )
        if "user_key" not in user_columns:
            batch_op.add_column(
                sa.Column("user_key", sa.LargeBinary(), nullable=True)
            )  # Temporary field
        if "kms_encrypted_dek" not in user_columns:
            batch_op.add_column(
                sa.Column("kms_encrypted_dek", sa.LargeBinary(), nullable=True)
            )


def downgrade() -> None:
    # This function correctly reverses the changes
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("kms_encrypted_dek")
        batch_op.drop_column("user_key")
        batch_op.drop_column("user_encrypted_dek")
        batch_op.drop_column("salt")
