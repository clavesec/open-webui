"""Tests for open_webui.models.db_encryption_shim — encrypt-on-flush / decrypt-on-load."""

import os
import copy
import pytest
from unittest.mock import MagicMock, patch

# Set env before importing modules
os.environ.setdefault("TPAI_LOCAL_HMAC_KEY", "test-hmac-key-for-unit-tests-32b!")

from cryptography.fernet import Fernet

from open_webui.utils.encryption_utils import (
    current_user_dek_context,
    encrypt_message,
    generate_dek,
    get_key,
)
from open_webui.models.db_encryption_shim import (
    _traverse_and_encrypt,
    _traverse_and_decrypt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chat_obj(messages=None, history_messages=None):
    """Create a mock Chat object with the given messages structure."""
    chat = {}
    if messages is not None:
        chat["messages"] = messages
    if history_messages is not None:
        chat["history"] = {"messages": history_messages}

    obj = MagicMock()
    obj.chat = chat
    return obj


def _with_dek(fn):
    """Run fn with a temporary DEK in context."""
    dek = generate_dek()
    token = current_user_dek_context.set(dek)
    try:
        return fn()
    finally:
        current_user_dek_context.reset(token)


# ---------------------------------------------------------------------------
# Encrypt on flush
# ---------------------------------------------------------------------------


class TestTraverseAndEncrypt:

    def test_encrypts_plaintext_messages(self):
        obj = _make_chat_obj(
            messages=[
                {"role": "user", "content": "Hello world"},
                {"role": "assistant", "content": "Hi there"},
            ]
        )

        def do():
            _traverse_and_encrypt(obj)

        _with_dek(do)

        for msg in obj.chat["messages"]:
            assert isinstance(msg["content"], dict)
            assert msg["content"]["is_encrypted"] is True
            assert "ciphertext" in msg["content"]

    def test_skips_already_encrypted(self):
        encrypted_content = {"ciphertext": "abc123", "is_encrypted": True}
        obj = _make_chat_obj(messages=[{"role": "user", "content": encrypted_content}])

        def do():
            _traverse_and_encrypt(obj)

        _with_dek(do)

        # Should be unchanged
        assert obj.chat["messages"][0]["content"] == encrypted_content

    def test_no_double_encryption(self):
        """Encrypting twice should not wrap the ciphertext again."""
        obj = _make_chat_obj(messages=[{"role": "user", "content": "test data"}])

        dek = generate_dek()
        token = current_user_dek_context.set(dek)
        try:
            _traverse_and_encrypt(obj)
            first_ct = obj.chat["messages"][0]["content"]["ciphertext"]

            # Encrypt again — should skip (already encrypted dict)
            _traverse_and_encrypt(obj)
            assert obj.chat["messages"][0]["content"]["ciphertext"] == first_ct
        finally:
            current_user_dek_context.reset(token)

    def test_skips_when_no_dek(self):
        """Without a DEK, content stays as plaintext string."""
        assert get_key() is None  # precondition
        obj = _make_chat_obj(messages=[{"role": "user", "content": "plain text"}])
        _traverse_and_encrypt(obj)
        assert obj.chat["messages"][0]["content"] == "plain text"

    def test_missing_content_key_no_crash(self):
        """Messages without 'content' key should not crash."""
        obj = _make_chat_obj(messages=[{"role": "system"}])

        def do():
            _traverse_and_encrypt(obj)

        _with_dek(do)
        # No exception — message is unchanged
        assert "content" not in obj.chat["messages"][0]

    def test_none_content_no_crash(self):
        """Messages with content=None should not crash."""
        obj = _make_chat_obj(messages=[{"role": "user", "content": None}])

        def do():
            _traverse_and_encrypt(obj)

        _with_dek(do)
        assert obj.chat["messages"][0]["content"] is None

    def test_empty_content_skipped(self):
        obj = _make_chat_obj(messages=[{"role": "user", "content": ""}])

        def do():
            _traverse_and_encrypt(obj)

        _with_dek(do)
        assert obj.chat["messages"][0]["content"] == ""

    def test_encrypts_history_messages(self):
        obj = _make_chat_obj(
            history_messages={
                "msg-1": {"role": "user", "content": "history content"},
            }
        )

        def do():
            _traverse_and_encrypt(obj)

        _with_dek(do)

        msg = obj.chat["history"]["messages"]["msg-1"]
        assert isinstance(msg["content"], dict)
        assert msg["content"]["is_encrypted"] is True

    def test_history_missing_content_no_crash(self):
        """History messages without 'content' key should not crash."""
        obj = _make_chat_obj(history_messages={"msg-1": {"role": "system"}})

        def do():
            _traverse_and_encrypt(obj)

        _with_dek(do)
        assert "content" not in obj.chat["history"]["messages"]["msg-1"]


# ---------------------------------------------------------------------------
# Decrypt on load
# ---------------------------------------------------------------------------


class TestTraverseAndDecrypt:

    def test_decrypts_encrypted_messages(self):
        """Round-trip: encrypt then decrypt should recover plaintext."""
        obj = _make_chat_obj(messages=[{"role": "user", "content": "Hello world"}])

        dek = generate_dek()
        token = current_user_dek_context.set(dek)
        try:
            _traverse_and_encrypt(obj)
            assert isinstance(obj.chat["messages"][0]["content"], dict)

            _traverse_and_decrypt(obj)
            assert obj.chat["messages"][0]["content"] == "Hello world"
        finally:
            current_user_dek_context.reset(token)

    def test_plaintext_unchanged(self):
        """Plain string content (not a dict) is left alone."""
        obj = _make_chat_obj(messages=[{"role": "user", "content": "plain text"}])
        _traverse_and_decrypt(obj)
        assert obj.chat["messages"][0]["content"] == "plain text"

    def test_preserves_ciphertext_on_decrypt_failure(self):
        """On decrypt failure, the encrypted dict is preserved (not replaced with error string)."""
        encrypted_dict = {"ciphertext": "invalid-ciphertext-data", "is_encrypted": True}
        obj = _make_chat_obj(
            messages=[{"role": "user", "content": copy.deepcopy(encrypted_dict)}]
        )

        # Use a DEK that won't match the ciphertext
        dek = generate_dek()
        token = current_user_dek_context.set(dek)
        try:
            _traverse_and_decrypt(obj)
        finally:
            current_user_dek_context.reset(token)

        # Content should still be the encrypted dict, NOT "[DECRYPTION ERROR]"
        content = obj.chat["messages"][0]["content"]
        assert isinstance(content, dict)
        assert content.get("is_encrypted") is True
        assert content.get("ciphertext") == "invalid-ciphertext-data"

    def test_history_preserves_ciphertext_on_failure(self):
        encrypted_dict = {"ciphertext": "bad-data", "is_encrypted": True}
        obj = _make_chat_obj(
            history_messages={
                "msg-1": {"role": "user", "content": copy.deepcopy(encrypted_dict)}
            }
        )

        dek = generate_dek()
        token = current_user_dek_context.set(dek)
        try:
            _traverse_and_decrypt(obj)
        finally:
            current_user_dek_context.reset(token)

        content = obj.chat["history"]["messages"]["msg-1"]["content"]
        assert isinstance(content, dict)
        assert content.get("is_encrypted") is True

    def test_missing_content_key_no_crash(self):
        obj = _make_chat_obj(messages=[{"role": "system"}])
        _traverse_and_decrypt(obj)
        assert "content" not in obj.chat["messages"][0]

    def test_history_missing_content_no_crash(self):
        obj = _make_chat_obj(history_messages={"msg-1": {"role": "system"}})
        _traverse_and_decrypt(obj)
        assert "content" not in obj.chat["history"]["messages"]["msg-1"]

    def test_decrypts_history_messages(self):
        obj = _make_chat_obj(
            history_messages={"msg-1": {"role": "user", "content": "secret history"}}
        )

        dek = generate_dek()
        token = current_user_dek_context.set(dek)
        try:
            _traverse_and_encrypt(obj)
            _traverse_and_decrypt(obj)
        finally:
            current_user_dek_context.reset(token)

        assert obj.chat["history"]["messages"]["msg-1"]["content"] == "secret history"
