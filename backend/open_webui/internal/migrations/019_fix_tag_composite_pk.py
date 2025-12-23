"""Peewee migrations -- 019_fix_tag_composite_pk.py

Fix tag schema - apply composite primary key (id, user_id)

Applies tag schema changes that were bypassed when billing enrollment
fields were added. This migration:
- Cleans up duplicate (id, user_id) pairs
- Creates composite primary key (id, user_id)
- Drops conflicting single-column constraints

Fixes: psycopg2.errors.UniqueViolation: could not create unique index "tag_id"
"""

from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator


with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    """Write your migrations here."""

    if isinstance(database, pw.SqliteDatabase):
        migrate_sqlite(migrator, database, fake=fake)
    else:
        migrate_postgresql(migrator, database, fake=fake)


def migrate_sqlite(migrator: Migrator, database: pw.SqliteDatabase, *, fake=False):
    """SQLite migration - uses table recreation approach."""

    # Step 1: Clean up duplicates (keep first occurrence)
    migrator.sql("""
        DELETE FROM tag
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM tag
            GROUP BY id, user_id
        )
    """)

    # Step 2: Create new table with composite primary key
    migrator.sql("""
        CREATE TABLE tag_new (
            id TEXT NOT NULL,
            name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            data TEXT,
            meta TEXT,
            created_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (id, user_id)
        )
    """)

    # Step 3: Copy data from old table
    migrator.sql("""
        INSERT INTO tag_new (id, name, user_id, data, meta, created_at, updated_at)
        SELECT id, name, user_id, data, meta, created_at, updated_at
        FROM tag
    """)

    # Step 4: Drop old table
    migrator.sql("DROP TABLE tag")

    # Step 5: Rename new table
    migrator.sql("ALTER TABLE tag_new RENAME TO tag")


def migrate_postgresql(migrator: Migrator, database: pw.Database, *, fake=False):
    """PostgreSQL migration."""

    # Step 1: Clean up duplicate (id, user_id) pairs
    migrator.sql("""
        DELETE FROM tag
        WHERE ctid NOT IN (
            SELECT MIN(ctid)
            FROM tag
            GROUP BY id, user_id
        )
    """)

    # Step 2: Drop old primary key (get name dynamically)
    migrator.sql("""
        DO $$
        DECLARE
            pk_name TEXT;
        BEGIN
            SELECT conname INTO pk_name
            FROM pg_constraint
            WHERE conrelid = 'tag'::regclass
              AND contype = 'p';

            IF pk_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE tag DROP CONSTRAINT %I', pk_name);
            END IF;
        END $$
    """)

    # Step 3: Drop conflicting unique constraints if they exist
    migrator.sql("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'tag'::regclass
                  AND contype = 'u'
                  AND conname = 'uq_id_user_id'
            ) THEN
                ALTER TABLE tag DROP CONSTRAINT uq_id_user_id;
            END IF;

            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'tag'::regclass
                  AND contype = 'u'
                  AND conname = 'tag_id'
            ) THEN
                ALTER TABLE tag DROP CONSTRAINT tag_id;
            END IF;
        END $$
    """)

    # Step 4: Drop unique indexes if they exist
    migrator.sql("DROP INDEX IF EXISTS tag_id")
    migrator.sql("DROP INDEX IF EXISTS uq_id_user_id")

    # Step 5: Create new composite primary key
    migrator.sql("ALTER TABLE tag ADD CONSTRAINT pk_id_user_id PRIMARY KEY (id, user_id)")


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    """Rollback migration (may fail if duplicate data exists)."""

    if isinstance(database, pw.SqliteDatabase):
        # Create table with old schema
        migrator.sql("""
            CREATE TABLE tag_old (
                id TEXT PRIMARY KEY NOT NULL,
                name TEXT NOT NULL,
                user_id TEXT NOT NULL,
                data TEXT,
                meta TEXT,
                created_at INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL DEFAULT 0,
                UNIQUE (id, user_id)
            )
        """)

        # Copy data
        migrator.sql("""
            INSERT INTO tag_old (id, name, user_id, data, meta, created_at, updated_at)
            SELECT id, name, user_id, data, meta, created_at, updated_at
            FROM tag
        """)

        # Swap tables
        migrator.sql("DROP TABLE tag")
        migrator.sql("ALTER TABLE tag_old RENAME TO tag")
    else:
        # PostgreSQL rollback
        migrator.sql("ALTER TABLE tag DROP CONSTRAINT pk_id_user_id")
        migrator.sql("ALTER TABLE tag ADD PRIMARY KEY (id)")
        migrator.sql("ALTER TABLE tag ADD CONSTRAINT uq_id_user_id UNIQUE (id, user_id)")
