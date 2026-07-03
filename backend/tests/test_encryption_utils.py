"""Tests for open_webui.utils.encryption_utils — per-user encryption primitives."""

import os
import pytest
from unittest.mock import patch
from cryptography.fernet import Fernet, InvalidToken

# We need TPAI_LOCAL_HMAC_KEY set before importing the module
os.environ.setdefault("TPAI_LOCAL_HMAC_KEY", "test-hmac-key-for-unit-tests-32b!")

from open_webui.utils.encryption_utils import (
    current_user_dek_context,
    decrypt_dek,
    decrypt_message,
    derive_key_from_user_id,
    encrypt_dek,
    encrypt_message,
    generate_dek,
    generate_salt,
    generate_user_id,
    get_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user_dek():
    """Generate a DEK and matching user key for tests."""
    dek = generate_dek()
    salt = generate_salt()
    user_key = derive_key_from_user_id("test-user-id", salt)
    encrypted_dek = encrypt_dek(dek, user_key)
    return dek, user_key, encrypted_dek


# ---------------------------------------------------------------------------
# get_key / no-DEK passthrough
# ---------------------------------------------------------------------------


class TestGetKey:
    def test_returns_none_when_no_context(self):
        assert get_key() is None

    def test_returns_dek_when_set(self):
        dek = generate_dek()
        token = current_user_dek_context.set(dek)
        try:
            assert get_key() == dek
        finally:
            current_user_dek_context.reset(token)


class TestNoDekPassthrough:
    """When no DEK is set, encrypt/decrypt are identity functions."""

    def test_encrypt_returns_plaintext(self):
        assert get_key() is None  # precondition
        assert encrypt_message("hello world") == "hello world"

    def test_decrypt_returns_input(self):
        assert get_key() is None
        assert decrypt_message("anything") == "anything"

    def test_encrypt_empty_string(self):
        assert encrypt_message("") == ""

    def test_decrypt_empty_string(self):
        assert decrypt_message("") == ""


# ---------------------------------------------------------------------------
# PBKDF2 determinism
# ---------------------------------------------------------------------------


class TestDeriveKey:
    def test_deterministic(self):
        salt = generate_salt()
        k1 = derive_key_from_user_id("user-abc", salt)
        k2 = derive_key_from_user_id("user-abc", salt)
        assert k1 == k2

    def test_different_salt_different_key(self):
        k1 = derive_key_from_user_id("user-abc", generate_salt())
        k2 = derive_key_from_user_id("user-abc", generate_salt())
        assert k1 != k2

    def test_different_user_different_key(self):
        salt = generate_salt()
        k1 = derive_key_from_user_id("user-a", salt)
        k2 = derive_key_from_user_id("user-b", salt)
        assert k1 != k2


# ---------------------------------------------------------------------------
# DEK encrypt / decrypt round-trip
# ---------------------------------------------------------------------------


class TestDekRoundTrip:
    def test_round_trip(self):
        dek, user_key, encrypted_dek = _make_user_dek()
        assert decrypt_dek(encrypted_dek, user_key) == dek

    def test_wrong_key_raises(self):
        dek, _, encrypted_dek = _make_user_dek()
        wrong_key = os.urandom(32)
        with pytest.raises(InvalidToken):
            decrypt_dek(encrypted_dek, wrong_key)


# ---------------------------------------------------------------------------
# Message encrypt / decrypt with DEK
# ---------------------------------------------------------------------------


class TestMessageEncryption:
    def test_round_trip(self):
        dek = generate_dek()
        token = current_user_dek_context.set(dek)
        try:
            ct = encrypt_message("secret message")
            assert ct != "secret message"
            assert decrypt_message(ct) == "secret message"
        finally:
            current_user_dek_context.reset(token)

    def test_cross_user_isolation(self):
        dek_a = generate_dek()
        dek_b = generate_dek()

        token_a = current_user_dek_context.set(dek_a)
        ct_a = encrypt_message("shared secret")
        current_user_dek_context.reset(token_a)

        # Decrypt with wrong key should NOT return plaintext
        token_b = current_user_dek_context.set(dek_b)
        result = decrypt_message(ct_a)
        current_user_dek_context.reset(token_b)

        # decrypt_message returns original ciphertext on failure (not plaintext)
        assert result != "shared secret"

    def test_empty_string_passthrough(self):
        dek = generate_dek()
        token = current_user_dek_context.set(dek)
        try:
            assert encrypt_message("") == ""
            assert decrypt_message("") == ""
        finally:
            current_user_dek_context.reset(token)


# ---------------------------------------------------------------------------
# generate_user_id
# ---------------------------------------------------------------------------


class TestGenerateUserId:
    def test_deterministic(self):
        key = os.urandom(32)
        assert generate_user_id("a@b.com", key) == generate_user_id("a@b.com", key)

    def test_case_insensitive(self):
        key = os.urandom(32)
        assert generate_user_id("A@B.COM", key) == generate_user_id("a@b.com", key)

    def test_different_emails(self):
        key = os.urandom(32)
        assert generate_user_id("a@b.com", key) != generate_user_id("x@y.com", key)

    def test_hex_digest_length(self):
        key = os.urandom(32)
        uid = generate_user_id("test@test.com", key)
        assert len(uid) == 64  # SHA256 hex
