import json
import logging
import os
from contextlib import contextmanager
from typing import Any, Optional

from open_webui.internal.wrappers import register_connection
from open_webui.env import (
    OPEN_WEBUI_DIR,
    DATABASE_URL,
    DATABASE_SCHEMA,
    SRC_LOG_LEVELS,
    DATABASE_POOL_MAX_OVERFLOW,
    DATABASE_POOL_RECYCLE,
    DATABASE_POOL_SIZE,
    DATABASE_POOL_TIMEOUT,
    ENABLE_AWS_RDS_IAM,
    AWS_REGION,
    PG_SSLMODE,
    PG_SSLROOTCERT,
)

# CRITICAL DEBUG: Check SSL env vars at module import time
log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["DB"])
log.info(
    f"🔍 CRITICAL DEBUG DB.PY IMPORT: PG_SSLMODE={PG_SSLMODE}, PG_SSLROOTCERT={PG_SSLROOTCERT}"
)
log.info(f"🔍 CRITICAL DEBUG DB.PY IMPORT: DATABASE_URL={DATABASE_URL[:50]}...")
from peewee_migrate import Router
from sqlalchemy import Dialect, create_engine, MetaData, types
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import QueuePool, NullPool
from sqlalchemy.sql.type_api import _T
from typing_extensions import Self

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["DB"])


class JSONField(types.TypeDecorator):
    impl = types.Text
    cache_ok = True

    def process_bind_param(self, value: Optional[_T], dialect: Dialect) -> Any:
        return json.dumps(value)

    def process_result_value(self, value: Optional[_T], dialect: Dialect) -> Any:
        if value is not None:
            return json.loads(value)

    def copy(self, **kw: Any) -> Self:
        return JSONField(self.impl.length)

    def db_value(self, value):
        return json.dumps(value)

    def python_value(self, value):
        if value is not None:
            return json.loads(value)


def mark_existing_migrations_complete(db):
    """Mark existing migrations as complete when tables already exist."""
    try:
        from datetime import datetime

        # Check if core tables exist (auth, user, chat, tag)
        cursor = db.execute_sql("""
            SELECT COUNT(*) FROM pg_tables
            WHERE schemaname = 'public'
            AND tablename IN ('auth', 'user', 'chat', 'tag')
        """)
        core_tables_count = cursor.fetchone()[0]

        if core_tables_count < 4:
            print("🗄️ PRE_MIGRATION: Core tables don't exist yet, migrations will create them")
            log.info("🗄️ PRE_MIGRATION: Core tables don't exist yet, migrations will create them")
            return

        print(f"🗄️ PRE_MIGRATION: Found {core_tables_count} core tables, checking migration tracking...")
        log.info(f"🗄️ PRE_MIGRATION: Found {core_tables_count} core tables, checking migration tracking...")

        # Check if migratehistory table exists
        cursor = db.execute_sql("""
            SELECT EXISTS (
                SELECT FROM pg_tables
                WHERE schemaname = 'public' AND tablename = 'migratehistory'
            )
        """)
        migrate_table_exists = cursor.fetchone()[0]

        if not migrate_table_exists:
            print("🗄️ PRE_MIGRATION: Creating migratehistory table...")
            log.info("🗄️ PRE_MIGRATION: Creating migratehistory table...")
            db.execute_sql("""
                CREATE TABLE migratehistory (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    migrated_at TIMESTAMP NOT NULL
                )
            """)

        # Check which migrations are already recorded
        cursor = db.execute_sql("SELECT name FROM migratehistory ORDER BY name")
        recorded_migrations = [row[0] for row in cursor.fetchall()]
        print(f"🗄️ PRE_MIGRATION: Currently recorded migrations: {recorded_migrations}")
        log.info(f"🗄️ PRE_MIGRATION: Currently recorded migrations: {recorded_migrations}")

        # List of migrations that should be marked complete if tables exist
        # (001-018 are the migrations that existed before our fix)
        expected_migrations = [
            '001_initial_schema',
            '002_add_local_sharing',
            '003_add_auth_api_key',
            '004_add_archived',
            '005_add_updated_at',
            '006_migrate_timestamps_and_charfields',
            '007_add_user_last_active_at',
            '008_add_memory',
            '009_add_models',
            '010_migrate_modelfiles_to_models',
            '011_add_user_settings',
            '012_add_tools',
            '013_add_user_info',
            '014_add_files',
            '015_add_functions',
            '016_add_valves_and_is_active',
            '017_add_user_oauth_sub',
            '018_add_function_is_global',
        ]

        # Delete old incorrect migration names that don't match actual files
        old_incorrect_names = [
            '004_add_chat_sharing',
            '005_add_user_info',
            '006_add_chat_tags',
            '007_add_metadata_to_user',
            '008_add_model_filter_config',
            '009_add_model_config',
            '010_add_tags_table_and_chat_tags',
            '011_add_user_last_active_at',
            '012_add_prompt',
            '013_add_archived_flag_to_chat',
            '014_add_local_sharing_to_chat',
            '015_add_chat_metadata',
            '016_add_file_model',
            '017_add_ollama_model_details',
            '018_add_modelfile_model_metadata',
        ]

        for old_name in old_incorrect_names:
            if old_name in recorded_migrations:
                print(f"🗄️ PRE_MIGRATION: Removing incorrect migration name: {old_name}")
                log.info(f"🗄️ PRE_MIGRATION: Removing incorrect migration name: {old_name}")
                # Use ? as placeholder for SQLite/Peewee compatibility, it gets auto-converted for PostgreSQL
                cursor = db.execute_sql("DELETE FROM migratehistory WHERE name = ?", (old_name,))
                cursor.close()

        print("🗄️ PRE_MIGRATION: ✅ Cleaned up old incorrect migration names")
        log.info("🗄️ PRE_MIGRATION: ✅ Cleaned up old incorrect migration names")

        # Mark missing migrations as complete
        now = datetime.now()
        migrations_marked = 0
        for migration_name in expected_migrations:
            if migration_name not in recorded_migrations:
                print(f"🗄️ PRE_MIGRATION: Marking {migration_name} as complete...")
                log.info(f"🗄️ PRE_MIGRATION: Marking {migration_name} as complete...")
                db.execute_sql(
                    "INSERT INTO migratehistory (name, migrated_at) VALUES (%s, %s)",
                    (migration_name, now)
                )
                migrations_marked += 1

        if migrations_marked > 0:
            print(f"🗄️ PRE_MIGRATION: ✅ Marked {migrations_marked} migrations as complete")
            log.info(f"🗄️ PRE_MIGRATION: ✅ Marked {migrations_marked} migrations as complete")
        else:
            print("🗄️ PRE_MIGRATION: All expected migrations already recorded")
            log.info("🗄️ PRE_MIGRATION: All expected migrations already recorded")

    except Exception as e:
        print(f"🗄️ PRE_MIGRATION: ⚠️  Error marking migrations complete: {e}")
        log.warning(f"🗄️ PRE_MIGRATION: ⚠️  Error marking migrations complete: {e}")
        log.exception("🗄️ PRE_MIGRATION: Full error traceback:")
        # Don't raise - let migrations continue even if this fails


def fix_tag_table_schema(db):
    """Fix tag table schema issues before running migrations."""
    try:
        # Check if tag table exists
        cursor = db.execute_sql("""
            SELECT EXISTS (
                SELECT FROM pg_tables
                WHERE schemaname = 'public' AND tablename = 'tag'
            )
        """)
        tag_exists = cursor.fetchone()[0]

        if not tag_exists:
            print("🗄️ PRE_MIGRATION: Tag table does not exist yet, skipping fix")
            log.info("🗄️ PRE_MIGRATION: Tag table does not exist yet, skipping fix")
            return

        print("🗄️ PRE_MIGRATION: Tag table exists, checking for schema issues...")
        log.info("🗄️ PRE_MIGRATION: Tag table exists, checking for schema issues...")

        # Check for duplicate (id, user_id) pairs
        cursor = db.execute_sql("""
            SELECT id, user_id, COUNT(*) as cnt
            FROM tag
            GROUP BY id, user_id
            HAVING COUNT(*) > 1
        """)
        duplicates = cursor.fetchall()

        if duplicates:
            print(f"🗄️ PRE_MIGRATION: Found {len(duplicates)} duplicate (id, user_id) pairs, cleaning up...")
            log.warning(f"🗄️ PRE_MIGRATION: Found {len(duplicates)} duplicate (id, user_id) pairs, cleaning up...")

            # Delete duplicates, keeping first occurrence
            db.execute_sql("""
                DELETE FROM tag
                WHERE ctid NOT IN (
                    SELECT MIN(ctid)
                    FROM tag
                    GROUP BY id, user_id
                )
            """)
            print(f"🗄️ PRE_MIGRATION: ✅ Cleaned up duplicate records")
            log.info(f"🗄️ PRE_MIGRATION: ✅ Cleaned up duplicate records")

        # Check current primary key
        cursor = db.execute_sql("""
            SELECT conname, pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            WHERE c.conrelid = 'tag'::regclass
              AND c.contype = 'p'
        """)
        pk_info = cursor.fetchone()

        if pk_info:
            pk_name, pk_def = pk_info
            print(f"🗄️ PRE_MIGRATION: Current primary key: {pk_name} - {pk_def}")
            log.info(f"🗄️ PRE_MIGRATION: Current primary key: {pk_name} - {pk_def}")

            # Check if it's already a composite key
            if '(id, user_id)' in pk_def or '(user_id, id)' in pk_def:
                print("🗄️ PRE_MIGRATION: ✅ Tag table already has composite primary key")
                log.info("🗄️ PRE_MIGRATION: ✅ Tag table already has composite primary key")
                return

            # Drop old primary key
            print(f"🗄️ PRE_MIGRATION: Dropping old primary key: {pk_name}")
            log.info(f"🗄️ PRE_MIGRATION: Dropping old primary key: {pk_name}")
            db.execute_sql(f'ALTER TABLE tag DROP CONSTRAINT "{pk_name}"')

        # Drop conflicting unique constraints and indexes
        for constraint_name in ['uq_id_user_id', 'tag_id']:
            cursor = db.execute_sql("""
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'tag'::regclass
                  AND contype = 'u'
                  AND conname = %s
            """, (constraint_name,))
            if cursor.fetchone():
                print(f"🗄️ PRE_MIGRATION: Dropping unique constraint: {constraint_name}")
                log.info(f"🗄️ PRE_MIGRATION: Dropping unique constraint: {constraint_name}")
                db.execute_sql(f'ALTER TABLE tag DROP CONSTRAINT "{constraint_name}"')

        # Drop unique indexes
        for index_name in ['tag_id', 'uq_id_user_id']:
            db.execute_sql(f'DROP INDEX IF EXISTS "{index_name}"')

        # Create composite primary key
        print("🗄️ PRE_MIGRATION: Creating composite primary key (id, user_id)...")
        log.info("🗄️ PRE_MIGRATION: Creating composite primary key (id, user_id)...")
        db.execute_sql('ALTER TABLE tag ADD CONSTRAINT pk_id_user_id PRIMARY KEY (id, user_id)')
        print("🗄️ PRE_MIGRATION: ✅ Created composite primary key")
        log.info("🗄️ PRE_MIGRATION: ✅ Created composite primary key")

    except Exception as e:
        print(f"🗄️ PRE_MIGRATION: ⚠️  Error during pre-migration fix: {e}")
        log.warning(f"🗄️ PRE_MIGRATION: ⚠️  Error during pre-migration fix: {e}")
        log.exception("🗄️ PRE_MIGRATION: Full error traceback:")
        # Don't raise - let migrations continue even if pre-fix fails


def fix_alembic_config_table(db):
    """
    Fix Alembic config table if it was dropped by bad migration.

    The migration 743f9468c8b1_add_billing_enrollment_fields.py was auto-generated
    incorrectly and dropped the config table in upgrade() instead of creating it.
    This function recreates the config table if needed and cleans up bad migration history.
    """
    try:
        # Check if config table exists
        cursor = db.execute_sql("""
            SELECT EXISTS (
                SELECT FROM pg_tables
                WHERE schemaname = 'public' AND tablename = 'config'
            )
        """)
        config_exists = cursor.fetchone()[0]

        if config_exists:
            print("🗄️ PRE_MIGRATION: Config table exists, no fix needed")
            log.info("🗄️ PRE_MIGRATION: Config table exists, no fix needed")
        else:
            print("🗄️ PRE_MIGRATION: Config table missing! Recreating...")
            log.warning("🗄️ PRE_MIGRATION: Config table missing! Recreating...")

            # Recreate config table (from migration ca81bd47c050_add_config_table.py)
            db.execute_sql("""
                CREATE TABLE config (
                    id SERIAL PRIMARY KEY,
                    data JSON NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            print("🗄️ PRE_MIGRATION: ✅ Recreated config table")
            log.info("🗄️ PRE_MIGRATION: ✅ Recreated config table")

        # Check if alembic_version table exists
        cursor = db.execute_sql("""
            SELECT EXISTS (
                SELECT FROM pg_tables
                WHERE schemaname = 'public' AND tablename = 'alembic_version'
            )
        """)
        alembic_version_exists = cursor.fetchone()[0]

        if alembic_version_exists:
            # Check for bad migration 743f9468c8b1
            cursor = db.execute_sql("""
                SELECT version_num FROM alembic_version
                WHERE version_num = '743f9468c8b1'
            """)
            bad_migration = cursor.fetchone()

            if bad_migration:
                print("🗄️ PRE_MIGRATION: Removing bad migration 743f9468c8b1 from alembic_version...")
                log.warning("🗄️ PRE_MIGRATION: Removing bad migration 743f9468c8b1 from alembic_version...")
                db.execute_sql("""
                    DELETE FROM alembic_version
                    WHERE version_num = '743f9468c8b1'
                """)
                print("🗄️ PRE_MIGRATION: ✅ Removed bad migration from history")
                log.info("🗄️ PRE_MIGRATION: ✅ Removed bad migration from history")

    except Exception as e:
        print(f"🗄️ PRE_MIGRATION: ⚠️  Error fixing config table: {e}")
        log.warning(f"🗄️ PRE_MIGRATION: ⚠️  Error fixing config table: {e}")
        log.exception("🗄️ PRE_MIGRATION: Full error traceback:")
        # Don't raise - let migrations continue even if this fails


def fix_missing_function_table(db):
    """
    Fix missing function table if it was never created.

    The mark_existing_migrations_complete() function marks Peewee migration
    015_add_functions as complete when core tables exist, even if the function
    table doesn't exist. This function creates the table if needed.
    """
    try:
        cursor = db.execute_sql("""
            SELECT EXISTS (
                SELECT FROM pg_tables
                WHERE schemaname = 'public' AND tablename = 'function'
            )
        """)
        function_exists = cursor.fetchone()[0]

        if function_exists:
            print("🗄️ PRE_MIGRATION: Function table exists, no fix needed")
            log.info("🗄️ PRE_MIGRATION: Function table exists, no fix needed")
            return

        print("🗄️ PRE_MIGRATION: Function table missing! Creating...")
        log.warning("🗄️ PRE_MIGRATION: Function table missing! Creating...")

        # Create function table (schema from 015_add_functions.py and 7e5b5dc7342b_init.py)
        db.execute_sql("""
            CREATE TABLE function (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                name TEXT,
                type TEXT,
                content TEXT,
                meta TEXT,
                valves TEXT,
                is_active BOOLEAN,
                is_global BOOLEAN,
                updated_at BIGINT,
                created_at BIGINT
            )
        """)
        print("🗄️ PRE_MIGRATION: ✅ Created function table")
        log.info("🗄️ PRE_MIGRATION: ✅ Created function table")

    except Exception as e:
        print(f"🗄️ PRE_MIGRATION: ⚠️  Error fixing function table: {e}")
        log.warning(f"🗄️ PRE_MIGRATION: ⚠️  Error fixing function table: {e}")
        log.exception("🗄️ PRE_MIGRATION: Full error traceback:")
        # Don't raise - let migrations continue even if this fails


# Workaround to handle the peewee migration
# This is required to ensure the peewee migration is handled before the alembic migration
def handle_peewee_migration(DATABASE_URL):
    # Temporary skip for manual database fix
    if os.environ.get("SKIP_PEEWEE_MIGRATIONS") == "true":
        print("🗄️ DB_MIGRATION: ⚠️  SKIPPING Peewee migrations (SKIP_PEEWEE_MIGRATIONS=true)")
        log.warning("🗄️ DB_MIGRATION: ⚠️  SKIPPING Peewee migrations (SKIP_PEEWEE_MIGRATIONS=true)")
        return

    db = None
    try:
        print("🗄️ DB_MIGRATION: Starting Peewee migration process...")
        log.info("🗄️ DB_MIGRATION: Starting Peewee migration process...")

        # Replace the postgresql:// with postgres:// to handle the peewee migration
        db_url = DATABASE_URL.replace("postgresql://", "postgres://")
        print(f"🗄️ DB_MIGRATION: Migration DB URL: {db_url[:60]}...")
        log.info(f"🗄️ DB_MIGRATION: Migration DB URL: {db_url[:60]}...")

        print("🗄️ DB_MIGRATION: Attempting database connection for migration...")
        log.info("🗄️ DB_MIGRATION: Attempting database connection for migration...")
        db = register_connection(db_url)
        print("🗄️ DB_MIGRATION: ✅ Database connection established for migration")
        log.info("🗄️ DB_MIGRATION: ✅ Database connection established for migration")

        migrate_dir = OPEN_WEBUI_DIR / "internal" / "migrations"
        print(f"🗄️ DB_MIGRATION: Migration directory: {migrate_dir}")
        log.info(f"🗄️ DB_MIGRATION: Migration directory: {migrate_dir}")
        print(f"🗄️ DB_MIGRATION: Migration directory exists: {migrate_dir.exists()}")
        log.info(f"🗄️ DB_MIGRATION: Migration directory exists: {migrate_dir.exists()}")

        if migrate_dir.exists():
            migration_files = list(migrate_dir.glob("*.py"))
            print(f"🗄️ DB_MIGRATION: Found {len(migration_files)} migration files")
            log.info(f"🗄️ DB_MIGRATION: Found {len(migration_files)} migration files")
            for migration_file in migration_files:
                print(f"🗄️ DB_MIGRATION: Migration file: {migration_file.name}")
                log.info(f"🗄️ DB_MIGRATION: Migration file: {migration_file.name}")

        # Pre-migration fixes
        print("🗄️ DB_MIGRATION: Running pre-migration fixes...")
        log.info("🗄️ DB_MIGRATION: Running pre-migration fixes...")

        # First, mark existing migrations as complete if tables already exist
        mark_existing_migrations_complete(db)

        # Fix tag table schema issues
        fix_tag_table_schema(db)

        # Fix Alembic config table if it was dropped by bad migration
        fix_alembic_config_table(db)

        # Fix missing function table if it was never created
        fix_missing_function_table(db)

        print("🗄️ DB_MIGRATION: Creating migration router...")
        log.info("🗄️ DB_MIGRATION: Creating migration router...")
        router = Router(db, logger=log, migrate_dir=migrate_dir)

        print("🗄️ DB_MIGRATION: Starting migration router execution...")
        log.info("🗄️ DB_MIGRATION: Starting migration router execution...")
        router.run()
        print("🗄️ DB_MIGRATION: ✅ Migration completed successfully")
        log.info("🗄️ DB_MIGRATION: ✅ Migration completed successfully")

        log.info("🗄️ DB_MIGRATION: Closing database connection...")
        db.close()
        log.info("🗄️ DB_MIGRATION: ✅ Database connection closed")

    except Exception as e:
        log.error(
            f"🗄️ DB_MIGRATION: ❌ Failed to initialize the database connection: {e}"
        )
        log.exception("🗄️ DB_MIGRATION: Full migration error traceback:")
        raise
    finally:
        # Properly closing the database connection
        if db and not db.is_closed():
            log.info("🔍 DEBUG: Closing database connection...")
            db.close()

        # Assert if db connection has been closed
        if db:
            assert db.is_closed(), "Database connection is still open."
            log.info("🔍 DEBUG: Database connection closed successfully")


print("🗄️ DB_MODULE: About to call handle_peewee_migration...")
log.info("🗄️ DB_MODULE: About to call handle_peewee_migration...")
handle_peewee_migration(DATABASE_URL)
print("🗄️ DB_MODULE: ✅ handle_peewee_migration completed successfully")
log.info("🗄️ DB_MODULE: ✅ handle_peewee_migration completed successfully")


# Build SQLAlchemy connect args for SSL and IAM token if enabled
sqlalchemy_connect_args = {}

# Force SSL parameters if provided to avoid libpq trying default ~/.postgresql
if PG_SSLMODE:
    sqlalchemy_connect_args.setdefault("connect_args", {})
    sqlalchemy_connect_args["connect_args"]["sslmode"] = PG_SSLMODE
if PG_SSLROOTCERT:
    sqlalchemy_connect_args.setdefault("connect_args", {})
    sqlalchemy_connect_args["connect_args"]["sslrootcert"] = PG_SSLROOTCERT

# IAM auth: generate token as password when enabled
sqlalchemy_url = DATABASE_URL
if ENABLE_AWS_RDS_IAM and sqlalchemy_url.startswith("postgresql://"):
    try:
        import boto3
        from urllib.parse import urlparse, quote

        parsed = urlparse(sqlalchemy_url)
        username = parsed.username or ""
        host = parsed.hostname
        port = parsed.port or 5432
        if not AWS_REGION:
            raise ValueError("AWS_REGION must be set when ENABLE_AWS_RDS_IAM is true")
        rds = boto3.client("rds", region_name=AWS_REGION)
        token = rds.generate_db_auth_token(
            DBHostname=host, Port=port, DBUsername=username
        )
        # Reconstruct URL with token as password and ensure empty password is ok
        safe_user = quote(username) if username else ""
        safe_host = host
        new_netloc = f"{safe_user}:{quote(token)}@{safe_host}:{port}"
        sqlalchemy_url = parsed._replace(netloc=new_netloc).geturl()
        log.info("Using AWS RDS IAM token for PostgreSQL authentication")
    except Exception as e:
        log.exception(f"Failed to generate AWS RDS IAM token: {e}")
        raise

SQLALCHEMY_DATABASE_URL = sqlalchemy_url
if "sqlite" in SQLALCHEMY_DATABASE_URL:
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    if DATABASE_POOL_SIZE > 0:
        engine = create_engine(
            SQLALCHEMY_DATABASE_URL,
            pool_size=DATABASE_POOL_SIZE,
            max_overflow=DATABASE_POOL_MAX_OVERFLOW,
            pool_timeout=DATABASE_POOL_TIMEOUT,
            pool_recycle=DATABASE_POOL_RECYCLE,
            pool_pre_ping=True,
            poolclass=QueuePool,
            **sqlalchemy_connect_args,
        )
    else:
        engine = create_engine(
            SQLALCHEMY_DATABASE_URL,
            pool_pre_ping=True,
            poolclass=NullPool,
            **sqlalchemy_connect_args,
        )


SessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
)
metadata_obj = MetaData(schema=DATABASE_SCHEMA)
Base = declarative_base(metadata=metadata_obj)
Session = scoped_session(SessionLocal)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


get_db = contextmanager(get_session)
