"""Fix tag schema - apply bypassed composite primary key

Revision ID: d1e2f3g4h5i6
Revises: 743f9468c8b1
Create Date: 2025-12-23 10:30:00.000000

Applies tag schema changes from migrations that were bypassed:
- 1af9b942657b_migrate_tags.py (unique constraint)
- 3ab32c4b8f59_update_tags.py (composite primary key)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "d1e2f3g4h5i6"
down_revision = "743f9468c8b1"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)

    # Check if tag table exists
    tables = inspector.get_table_names()
    if "tag" not in tables:
        print("Tag table not found, skipping migration")
        return

    # Get current schema state
    current_pk = inspector.get_pk_constraint("tag")
    current_constraints = inspector.get_unique_constraints("tag")
    existing_indexes = inspector.get_indexes("tag")

    print(f"Current PK: {current_pk}")
    print(f"Current unique constraints: {current_constraints}")

    # Check if already migrated
    if current_pk and current_pk.get("constrained_columns") == ["id", "user_id"]:
        print("Tag schema already has composite PK, skipping migration")
        return

    # STEP 1: Clean up duplicate (id, user_id) pairs
    duplicate_check_sql = """
        SELECT id, user_id, COUNT(*) as cnt
        FROM tag
        GROUP BY id, user_id
        HAVING COUNT(*) > 1
    """
    duplicates = conn.execute(sa.text(duplicate_check_sql)).fetchall()

    if duplicates:
        print(f"Found {len(duplicates)} duplicate (id, user_id) pairs - cleaning up")
        for dup in duplicates:
            # Keep first occurrence, delete rest
            if 'sqlite' in str(conn.engine.url):
                delete_sql = """
                    DELETE FROM tag
                    WHERE rowid NOT IN (
                        SELECT MIN(rowid)
                        FROM tag
                        WHERE id = :id AND user_id = :user_id
                    ) AND id = :id AND user_id = :user_id
                """
            else:  # PostgreSQL
                delete_sql = """
                    DELETE FROM tag
                    WHERE ctid NOT IN (
                        SELECT MIN(ctid)
                        FROM tag
                        WHERE id = :id AND user_id = :user_id
                    ) AND id = :id AND user_id = :user_id
                """
            result = conn.execute(sa.text(delete_sql), {"id": dup.id, "user_id": dup.user_id})
            print(f"  Deleted {result.rowcount} duplicate(s) for tag '{dup.id}' user '{dup.user_id}'")

    # STEP 2: Apply schema changes using batch mode (SQLite compatible)
    with op.batch_alter_table("tag", schema=None) as batch_op:
        # Drop existing primary key
        if current_pk and current_pk.get("constrained_columns"):
            pk_name = current_pk.get("name")
            if pk_name:
                print(f"Dropping old primary key: {pk_name}")
                batch_op.drop_constraint(pk_name, type_="primary")

        # Create new composite primary key
        print("Creating composite primary key (id, user_id)")
        batch_op.create_primary_key("pk_id_user_id", ["id", "user_id"])

        # Drop conflicting unique constraints
        for constraint in current_constraints:
            if constraint["name"] in ["uq_id_user_id", "tag_id"]:
                print(f"Dropping unique constraint: {constraint['name']}")
                batch_op.drop_constraint(constraint["name"], type_="unique")

        # Drop unique indexes not covered by constraints
        for index in existing_indexes:
            if index["unique"] and not any(
                constraint["name"] == index["name"] for constraint in current_constraints
            ):
                print(f"Dropping unique index: {index['name']}")
                batch_op.drop_index(index["name"])

    print("Tag schema migration completed successfully")


def downgrade():
    """Revert to single-column primary key (may fail if data violates old constraints)"""
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    current_pk = inspector.get_pk_constraint("tag")

    with op.batch_alter_table("tag", schema=None) as batch_op:
        # Drop composite primary key
        if current_pk and "pk_id_user_id" == current_pk.get("name"):
            batch_op.drop_constraint("pk_id_user_id", type_="primary")

        # Restore single-column primary key (WARNING: may fail with duplicate data)
        batch_op.create_primary_key("pk_id", ["id"])

        # Restore unique constraint
        batch_op.create_unique_constraint("uq_id_user_id", ["id", "user_id"])
