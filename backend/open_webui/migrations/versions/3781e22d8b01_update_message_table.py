"""Update message & channel tables

Revision ID: 3781e22d8b01
Revises: 7826ab40b532
Create Date: 2024-12-30 03:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "3781e22d8b01"
down_revision = "7826ab40b532"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    channel_columns = (
        {col["name"] for col in inspector.get_columns("channel")}
        if "channel" in existing_tables
        else set()
    )
    message_columns = (
        {col["name"] for col in inspector.get_columns("message")}
        if "message" in existing_tables
        else set()
    )

    # Add 'type' column to the 'channel' table
    if "type" not in channel_columns:
        op.add_column(
            "channel",
            sa.Column(
                "type",
                sa.Text(),
                nullable=True,
            ),
        )

    # Add 'parent_id' column to the 'message' table for threads
    if "parent_id" not in message_columns:
        op.add_column(
            "message",
            sa.Column("parent_id", sa.Text(), nullable=True),
        )

    if "message_reaction" not in existing_tables:
        op.create_table(
            "message_reaction",
            sa.Column(
                "id", sa.Text(), nullable=False, primary_key=True, unique=True
            ),  # Unique reaction ID
            sa.Column("user_id", sa.Text(), nullable=False),  # User who reacted
            sa.Column(
                "message_id", sa.Text(), nullable=False
            ),  # Message that was reacted to
            sa.Column(
                "name", sa.Text(), nullable=False
            ),  # Reaction name (e.g. "thumbs_up")
            sa.Column(
                "created_at", sa.BigInteger(), nullable=True
            ),  # Timestamp of when the reaction was added
        )

    if "channel_member" not in existing_tables:
        op.create_table(
            "channel_member",
            sa.Column(
                "id", sa.Text(), nullable=False, primary_key=True, unique=True
            ),  # Record ID for the membership row
            sa.Column("channel_id", sa.Text(), nullable=False),  # Associated channel
            sa.Column("user_id", sa.Text(), nullable=False),  # Associated user
            sa.Column(
                "created_at", sa.BigInteger(), nullable=True
            ),  # Timestamp of when the user joined the channel
        )


def downgrade():
    # Revert 'type' column addition to the 'channel' table
    op.drop_column("channel", "type")
    op.drop_column("message", "parent_id")
    op.drop_table("message_reaction")
    op.drop_table("channel_member")
