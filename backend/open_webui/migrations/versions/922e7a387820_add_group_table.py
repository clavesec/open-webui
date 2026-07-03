"""Add group table

Revision ID: 922e7a387820
Revises: 4ace53fd72c8
Create Date: 2024-11-14 03:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "922e7a387820"
down_revision = "4ace53fd72c8"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())

    if "group" not in existing_tables:
        op.create_table(
            "group",
            sa.Column("id", sa.Text(), nullable=False, primary_key=True, unique=True),
            sa.Column("user_id", sa.Text(), nullable=True),
            sa.Column("name", sa.Text(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("data", sa.JSON(), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("permissions", sa.JSON(), nullable=True),
            sa.Column("user_ids", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.BigInteger(), nullable=True),
            sa.Column("updated_at", sa.BigInteger(), nullable=True),
        )

    # Get existing columns for each table
    model_columns = (
        {col["name"] for col in inspector.get_columns("model")}
        if "model" in existing_tables
        else set()
    )
    knowledge_columns = (
        {col["name"] for col in inspector.get_columns("knowledge")}
        if "knowledge" in existing_tables
        else set()
    )
    prompt_columns = (
        {col["name"] for col in inspector.get_columns("prompt")}
        if "prompt" in existing_tables
        else set()
    )
    tool_columns = (
        {col["name"] for col in inspector.get_columns("tool")}
        if "tool" in existing_tables
        else set()
    )

    # Add 'access_control' column to 'model' table
    if "access_control" not in model_columns:
        op.add_column(
            "model",
            sa.Column("access_control", sa.JSON(), nullable=True),
        )

    # Add 'is_active' column to 'model' table
    if "is_active" not in model_columns:
        op.add_column(
            "model",
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.sql.expression.true(),
            ),
        )

    # Add 'access_control' column to 'knowledge' table
    if "access_control" not in knowledge_columns:
        op.add_column(
            "knowledge",
            sa.Column("access_control", sa.JSON(), nullable=True),
        )

    # Add 'access_control' column to 'prompt' table
    if "access_control" not in prompt_columns:
        op.add_column(
            "prompt",
            sa.Column("access_control", sa.JSON(), nullable=True),
        )

    # Add 'access_control' column to 'tools' table
    if "access_control" not in tool_columns:
        op.add_column(
            "tool",
            sa.Column("access_control", sa.JSON(), nullable=True),
        )


def downgrade():
    op.drop_table("group")

    # Drop 'access_control' column from 'model' table
    op.drop_column("model", "access_control")

    # Drop 'is_active' column from 'model' table
    op.drop_column("model", "is_active")

    # Drop 'access_control' column from 'knowledge' table
    op.drop_column("knowledge", "access_control")

    # Drop 'access_control' column from 'prompt' table
    op.drop_column("prompt", "access_control")

    # Drop 'access_control' column from 'tools' table
    op.drop_column("tool", "access_control")
