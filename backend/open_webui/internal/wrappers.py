import json
import logging
from contextvars import ContextVar
from urllib.parse import quote

from open_webui.env import (
    SRC_LOG_LEVELS,
    ENABLE_AWS_RDS_IAM,
    AWS_REGION,
    PG_SSLMODE,
    PG_SSLROOTCERT,
)
from peewee import *
from peewee import InterfaceError as PeeWeeInterfaceError
from peewee import PostgresqlDatabase
from playhouse.db_url import connect, parse
from playhouse.shortcuts import ReconnectMixin

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["DB"])

db_state_default = {"closed": None, "conn": None, "ctx": None, "transactions": None}
db_state = ContextVar("db_state", default=db_state_default.copy())


class PeeweeConnectionState(object):
    def __init__(self, **kwargs):
        super().__setattr__("_state", db_state)
        super().__init__(**kwargs)

    def __setattr__(self, name, value):
        self._state.get()[name] = value

    def __getattr__(self, name):
        value = self._state.get()[name]
        return value


class CustomReconnectMixin(ReconnectMixin):
    reconnect_errors = (
        # psycopg2
        (OperationalError, "termin"),
        (InterfaceError, "closed"),
        # peewee
        (PeeWeeInterfaceError, "closed"),
    )


class ReconnectingPostgresqlDatabase(CustomReconnectMixin, PostgresqlDatabase):
    pass


def _parse_json_database_url(db_url: str) -> str:
    """Parse JSON-formatted DATABASE_URL from AWS Secrets Manager.

    AWS Secrets Manager stores database credentials as JSON with keys:
    username, password, host, port, dbname, engine, etc.

    This function detects JSON format and constructs a proper PostgreSQL URL.

    Args:
        db_url: Either a standard PostgreSQL URL or JSON string from Secrets Manager

    Returns:
        PostgreSQL connection URL in standard format

    Examples:
        >>> _parse_json_database_url('{"username":"user","password":"pass","host":"db.example.com","port":5432,"dbname":"mydb"}')
        'postgresql://user:pass@db.example.com:5432/mydb'
        >>> _parse_json_database_url('postgresql://user:pass@localhost/db')
        'postgresql://user:pass@localhost/db'
    """
    # If it starts with postgres, it's already a URL
    if db_url.startswith(("postgres://", "postgresql://")):
        return db_url

    # Try to parse as JSON (Secrets Manager format)
    try:
        creds = json.loads(db_url)

        # Extract required fields
        username = creds.get("username", "")
        password = creds.get("password", "")
        host = creds.get("host", "localhost")
        port = creds.get("port", 5432)
        dbname = creds.get("dbname", creds.get("database", "postgres"))

        # URL-encode credentials to handle special characters
        safe_user = quote(str(username), safe="")
        safe_pass = quote(str(password), safe="")

        # Construct PostgreSQL URL
        url = f"postgresql://{safe_user}:{safe_pass}@{host}:{port}/{dbname}"

        log.info("Parsed JSON DATABASE_URL from Secrets Manager format")
        return url

    except (json.JSONDecodeError, TypeError, KeyError) as e:
        # Not valid JSON, return as-is and let downstream handle it
        log.debug(
            f"DATABASE_URL is not JSON format (this is expected for standard URLs): {e}"
        )
        return db_url


def _augment_postgres_url_with_iam_and_ssl(db_url: str) -> str:
    """Augment PostgreSQL URL with IAM authentication token and SSL parameters.

    When ENABLE_AWS_RDS_IAM is enabled, this function:
    1. Generates a temporary IAM authentication token using boto3
    2. Replaces the password in the URL with the IAM token
    3. URL-encodes credentials to handle special characters

    SSL parameters (sslmode, sslrootcert) are added if not already present
    in the URL and are configured via environment variables.

    Args:
        db_url: PostgreSQL connection URL (postgres:// or postgresql://)

    Returns:
        Augmented URL with IAM token and SSL parameters

    Raises:
        ValueError: If ENABLE_AWS_RDS_IAM is true but AWS_REGION is not set
        Exception: If IAM token generation fails (logged and re-raised)
    """
    if not db_url.startswith("postgres://") and not db_url.startswith("postgresql://"):
        return db_url
    final_url = db_url

    # Debug SSL environment variables
    log.debug(f"SSL ENV: PG_SSLMODE={PG_SSLMODE}, PG_SSLROOTCERT={PG_SSLROOTCERT}")
    log.debug(f"IAM ENV: ENABLE_AWS_RDS_IAM={ENABLE_AWS_RDS_IAM}, AWS_REGION={AWS_REGION}")

    try:
        from urllib.parse import urlparse, quote

        if ENABLE_AWS_RDS_IAM:
            import boto3

            parsed = urlparse(final_url)
            username = parsed.username or ""
            host = parsed.hostname
            port = parsed.port or 5432
            if not AWS_REGION:
                raise ValueError(
                    "AWS_REGION must be set when ENABLE_AWS_RDS_IAM is true"
                )
            rds = boto3.client("rds", region_name=AWS_REGION)
            token = rds.generate_db_auth_token(
                DBHostname=host, Port=port, DBUsername=username
            )
            safe_user = quote(username) if username else ""
            new_netloc = f"{safe_user}:{quote(token)}@{host}:{port}"
            final_url = parsed._replace(netloc=new_netloc).geturl()
        # add ssl query params
        if PG_SSLMODE and "sslmode=" not in final_url:
            sep = "&" if "?" in final_url else "?"
            final_url = f"{final_url}{sep}sslmode={PG_SSLMODE}"
            log.debug(f"Added sslmode={PG_SSLMODE} to URL")
        if PG_SSLROOTCERT and "sslrootcert=" not in final_url:
            sep = "&" if "?" in final_url else "?"
            final_url = f"{final_url}{sep}sslrootcert={PG_SSLROOTCERT}"
            log.debug(f"Added sslrootcert={PG_SSLROOTCERT} to URL")
    except Exception as e:
        log.exception(f"Failed to augment PostgreSQL URL for IAM/SSL: {e}")
        raise
    return final_url


def register_connection(db_url):
    """Register and configure a database connection from a URL.

    Handles three database URL formats:
    1. Standard PostgreSQL URL: postgresql://user:pass@host:port/db
    2. AWS Secrets Manager JSON: {"username":"...","password":"...","host":"..."}
    3. SQLite URL: sqlite:///path/to/db.sqlite

    For PostgreSQL connections, this function:
    - Parses JSON format if from AWS Secrets Manager
    - Augments URL with IAM token if enabled
    - Adds SSL parameters if configured
    - Creates a ReconnectingPostgresqlDatabase with auto-reconnect

    Args:
        db_url: Database connection URL or JSON credentials string

    Returns:
        Configured database connection (PostgresqlDatabase or SqliteDatabase)

    Raises:
        ValueError: If database type is not supported
        Exception: If connection fails
    """
    # SECURITY: Only log URL prefix to avoid exposing credentials
    log.info(f"DB_CONNECT: Starting register_connection with URL prefix: {db_url[:50]}...")

    # Parse JSON format if coming from AWS Secrets Manager
    parsed_url = _parse_json_database_url(db_url)
    if parsed_url != db_url:
        log.info("DB_CONNECT: Converted JSON Secrets Manager format to PostgreSQL URL")

    log.debug("DB_CONNECT: Augmenting URL with IAM/SSL...")
    augmented_url = _augment_postgres_url_with_iam_and_ssl(parsed_url)
    log.debug("DB_CONNECT: URL augmentation complete")

    log.debug("DB_CONNECT: Calling connect()...")
    db = connect(augmented_url, unquote_user=True, unquote_password=True)
    log.info("DB_CONNECT: Initial connection established")

    if isinstance(db, PostgresqlDatabase):
        log.debug("DB_CONNECT: Setting up PostgreSQL database...")
        # Enable autoconnect for PostgreSQL databases, managed by Peewee
        db.autoconnect = True
        db.reuse_if_open = True

        # Get the connection details from the AUGMENTED URL to preserve SSL params
        log.debug("DB_CONNECT: Parsing connection details...")
        connection = parse(augmented_url, unquote_user=True, unquote_password=True)
        log.debug("DB_CONNECT: Connection details parsed successfully")

        # Use our custom database class that supports reconnection
        log.debug("DB_CONNECT: Creating ReconnectingPostgresqlDatabase...")
        db = ReconnectingPostgresqlDatabase(**connection)
        db.connect(reuse_if_open=True)
        log.info("DB_CONNECT: PostgreSQL database connected successfully")
    elif isinstance(db, SqliteDatabase):
        # Enable autoconnect for SQLite databases, managed by Peewee
        db.autoconnect = True
        db.reuse_if_open = True
        log.info("DB_CONNECT: SQLite database connected successfully")
    else:
        raise ValueError(
            "Unsupported database connection type. Supported types: PostgreSQL, SQLite"
        )
    return db
