"""Unit tests for DATABASE_URL JSON parser.

Tests the _parse_json_database_url function which handles parsing
AWS Secrets Manager JSON format into PostgreSQL connection URLs.
"""

import pytest
from open_webui.internal.wrappers import _parse_json_database_url


class TestParseJsonDatabaseUrl:
    """Tests for _parse_json_database_url function."""

    def test_standard_postgresql_url_unchanged(self):
        """Standard PostgreSQL URLs should pass through unchanged."""
        url = "postgresql://user:pass@localhost:5432/testdb"
        assert _parse_json_database_url(url) == url

    def test_postgres_prefix_unchanged(self):
        """postgres:// prefix URLs should pass through unchanged."""
        url = "postgres://user:pass@localhost:5432/testdb"
        assert _parse_json_database_url(url) == url

    def test_json_format_parsed_correctly(self):
        """AWS Secrets Manager JSON format should be parsed correctly."""
        json_url = '{"username":"dbuser","password":"secret","host":"db.example.com","port":5432,"dbname":"mydb"}'
        result = _parse_json_database_url(json_url)
        assert result == "postgresql://dbuser:secret@db.example.com:5432/mydb"

    def test_json_with_special_characters_in_password(self):
        """Passwords with special characters should be URL-encoded."""
        json_url = '{"username":"user","password":"p@ss!word#123","host":"localhost","port":5432,"dbname":"db"}'
        result = _parse_json_database_url(json_url)
        # Special characters should be URL-encoded
        assert "p%40ss%21word%23123" in result

    def test_json_with_special_characters_in_username(self):
        """Usernames with special characters should be URL-encoded."""
        json_url = '{"username":"user@domain","password":"pass","host":"localhost","port":5432,"dbname":"db"}'
        result = _parse_json_database_url(json_url)
        # @ should be URL-encoded
        assert "user%40domain" in result

    def test_json_missing_port_uses_default(self):
        """Missing port should default to 5432."""
        json_url = '{"username":"user","password":"pass","host":"localhost","dbname":"db"}'
        result = _parse_json_database_url(json_url)
        assert ":5432/" in result

    def test_json_missing_host_uses_default(self):
        """Missing host should default to localhost."""
        json_url = '{"username":"user","password":"pass","port":5432,"dbname":"db"}'
        result = _parse_json_database_url(json_url)
        assert "@localhost:" in result

    def test_json_with_database_key_instead_of_dbname(self):
        """Should handle 'database' key as fallback for 'dbname'."""
        json_url = '{"username":"user","password":"pass","host":"localhost","port":5432,"database":"mydb"}'
        result = _parse_json_database_url(json_url)
        assert result.endswith("/mydb")

    def test_json_missing_dbname_uses_default(self):
        """Missing dbname should default to postgres."""
        json_url = '{"username":"user","password":"pass","host":"localhost","port":5432}'
        result = _parse_json_database_url(json_url)
        assert result.endswith("/postgres")

    def test_json_with_integer_port(self):
        """Integer port values should be handled correctly."""
        json_url = '{"username":"user","password":"pass","host":"localhost","port":5433,"dbname":"db"}'
        result = _parse_json_database_url(json_url)
        assert ":5433/" in result

    def test_json_with_string_port(self):
        """String port values should be handled correctly."""
        json_url = '{"username":"user","password":"pass","host":"localhost","port":"5433","dbname":"db"}'
        result = _parse_json_database_url(json_url)
        assert ":5433/" in result

    def test_invalid_json_returns_original(self):
        """Invalid JSON should return original string."""
        invalid = "not-json-at-all"
        assert _parse_json_database_url(invalid) == invalid

    def test_malformed_json_returns_original(self):
        """Malformed JSON should return original string."""
        malformed = '{"username": "user", "password":}'
        assert _parse_json_database_url(malformed) == malformed

    def test_empty_string_returns_empty(self):
        """Empty string should return empty."""
        assert _parse_json_database_url("") == ""

    def test_json_with_empty_credentials(self):
        """JSON with empty username/password should work."""
        json_url = '{"username":"","password":"","host":"localhost","port":5432,"dbname":"db"}'
        result = _parse_json_database_url(json_url)
        assert result == "postgresql://:@localhost:5432/db"

    def test_json_with_extra_fields_ignored(self):
        """Extra fields in JSON (like engine, etc.) should be ignored."""
        json_url = '{"username":"user","password":"pass","host":"localhost","port":5432,"dbname":"db","engine":"postgres","extra":"field"}'
        result = _parse_json_database_url(json_url)
        assert result == "postgresql://user:pass@localhost:5432/db"

    def test_real_world_rds_secret_format(self):
        """Test with realistic RDS secret format."""
        json_url = (
            '{"username":"admin","password":"MySecureP@ss123!","engine":"postgres",'
            '"host":"mydb.cluster-abcdefg.us-east-1.rds.amazonaws.com",'
            '"port":5432,"dbClusterIdentifier":"mydb","dbname":"owui"}'
        )
        result = _parse_json_database_url(json_url)
        assert "postgresql://admin:MySecureP%40ss123%21@" in result
        assert "mydb.cluster-abcdefg.us-east-1.rds.amazonaws.com:5432/owui" in result
