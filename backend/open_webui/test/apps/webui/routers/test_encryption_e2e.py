"""
E2E integration tests for per-user encryption through a real Postgres instance.

Validates the full path: user provisioning -> chat creation -> DB-level ciphertext
verification -> API/ORM decryption verification.

Requires Docker running locally (for Postgres container). No AWS credentials needed.

Run from Product/owui/backend/:
    PG_SSLMODE=disable PYTHONPATH=open_webui TPAI_LOCAL_HMAC_KEY=test-hmac-key-for-integration \
    python -m pytest open_webui/test/apps/webui/routers/test_encryption_e2e.py -v
"""

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

# Must be set before any open_webui imports trigger db.py / encryption_utils
os.environ.setdefault("TPAI_LOCAL_HMAC_KEY", "test-hmac-key-for-integration")
os.environ.setdefault("PG_SSLMODE", "disable")

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

from cryptography.fernet import Fernet, InvalidToken


class TestEncryptionE2E(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    @classmethod
    def setup_class(cls):
        super().setup_class()

    def setup_method(self):
        super().setup_method()

    # ── Helpers ──────────────────────────────────────────────────────────

    def _provision_encrypted_user(
        self,
        user_id: str,
        email_hmac: str,
        billing_id: str,
        name: str = "EncryptedUser",
    ):
        """Provision a billing-enrolled user with full encryption keys."""
        from open_webui.models.auths import Auths

        return Auths.insert_billing_enrolled_user(
            user_id=user_id,
            email_hmac=email_hmac,
            billing_customer_id=billing_id,
            name=name,
        )

    def _provision_plaintext_user(
        self,
        user_id: str,
        email: str,
        name: str = "PlaintextUser",
    ):
        """Create a legacy user with no encryption fields."""
        from open_webui.internal.db import get_db
        from open_webui.models.auths import Auth, AuthModel
        from open_webui.models.users import Users

        with get_db() as db:
            auth = Auth(
                **AuthModel(
                    id=user_id,
                    email=email,
                    password="hashed-pw",
                    active=True,
                ).model_dump()
            )
            db.add(auth)
            db.commit()

        return Users.insert_new_user(
            id=user_id,
            name=name,
            email=email,
            profile_image_url="/user.png",
            role="user",
        )

    def _get_raw_chat_json(self, chat_id: str) -> dict:
        """Read chat column via raw SQL, bypassing ORM load decryption hook."""
        from open_webui.internal.db import get_db
        from sqlalchemy import text

        with get_db() as db:
            row = db.execute(
                text("SELECT chat FROM chat WHERE id = :id"),
                {"id": chat_id},
            ).fetchone()
            assert row is not None, f"Chat {chat_id} not found in DB"
            raw = row[0]
            # Postgres JSON column returns dict directly
            return raw if isinstance(raw, dict) else json.loads(raw)

    def _create_chat_with_messages(self, user_id: str, messages: list[dict]) -> str:
        """Insert a chat via ORM and return the chat id."""
        from open_webui.models.chats import ChatForm, Chats

        chat = Chats.insert_new_chat(
            user_id,
            ChatForm(
                **{
                    "chat": {
                        "name": "test-chat",
                        "messages": messages,
                    }
                }
            ),
        )
        assert chat is not None
        return chat.id

    # ── Existing Test Cases ──────────────────────────────────────────────

    def test_encrypted_user_chat_roundtrip(self):
        """Encrypted user: raw SQL shows ciphertext, ORM read returns plaintext."""
        uid = str(uuid.uuid4())
        user = self._provision_encrypted_user(
            user_id=uid,
            email_hmac=f"hmac-{uid}",
            billing_id=f"billing-{uid}",
        )
        assert user is not None
        assert user.user_key is not None
        assert user.user_encrypted_dek is not None

        chat_id = self._create_chat_with_messages(
            user.id, [{"role": "user", "content": "Hello encrypted world"}]
        )

        # Raw SQL: content should be encrypted dict
        raw_chat = self._get_raw_chat_json(chat_id)
        raw_messages = raw_chat.get("messages", [])
        assert len(raw_messages) == 1
        content_at_rest = raw_messages[0]["content"]
        assert isinstance(content_at_rest, dict), "Content at rest should be an encrypted dict"
        assert content_at_rest.get("is_encrypted") is True
        assert "ciphertext" in content_at_rest
        assert content_at_rest["ciphertext"] != "Hello encrypted world"

        # ORM read: should transparently decrypt
        from open_webui.models.chats import Chats

        loaded = Chats.get_chat_by_id(chat_id)
        assert loaded is not None
        loaded_messages = loaded.chat.get("messages", [])
        assert len(loaded_messages) == 1
        assert loaded_messages[0]["content"] == "Hello encrypted world"

    def test_plaintext_user_chat_stored_unencrypted(self):
        """Legacy user: raw SQL shows plain string content, ORM returns same."""
        uid = str(uuid.uuid4())
        user = self._provision_plaintext_user(
            user_id=uid,
            email=f"{uid}@example.com",
        )
        assert user is not None

        chat_id = self._create_chat_with_messages(
            user.id, [{"role": "user", "content": "Hello plaintext world"}]
        )

        # Raw SQL: content should be a plain string
        raw_chat = self._get_raw_chat_json(chat_id)
        raw_messages = raw_chat.get("messages", [])
        assert len(raw_messages) == 1
        assert raw_messages[0]["content"] == "Hello plaintext world"

        # ORM read: same plain string
        from open_webui.models.chats import Chats

        loaded = Chats.get_chat_by_id(chat_id)
        assert loaded is not None
        assert loaded.chat["messages"][0]["content"] == "Hello plaintext world"

    def test_plaintext_and_encrypted_users_coexist(self):
        """Both user types coexist; encryption status correct for each."""
        uid_enc = str(uuid.uuid4())
        uid_plain = str(uuid.uuid4())
        enc_user = self._provision_encrypted_user(
            user_id=uid_enc,
            email_hmac=f"hmac-{uid_enc}",
            billing_id=f"billing-{uid_enc}",
        )
        plain_user = self._provision_plaintext_user(
            user_id=uid_plain,
            email=f"{uid_plain}@example.com",
        )

        enc_chat_id = self._create_chat_with_messages(
            enc_user.id, [{"role": "user", "content": "secret message"}]
        )
        plain_chat_id = self._create_chat_with_messages(
            plain_user.id, [{"role": "user", "content": "open message"}]
        )

        # Encrypted user: ciphertext at rest
        raw_enc = self._get_raw_chat_json(enc_chat_id)
        assert isinstance(raw_enc["messages"][0]["content"], dict)
        assert raw_enc["messages"][0]["content"]["is_encrypted"] is True

        # Plaintext user: plain string at rest
        raw_plain = self._get_raw_chat_json(plain_chat_id)
        assert raw_plain["messages"][0]["content"] == "open message"

        # Both read back correctly via ORM
        from open_webui.models.chats import Chats

        assert Chats.get_chat_by_id(enc_chat_id).chat["messages"][0]["content"] == "secret message"
        assert Chats.get_chat_by_id(plain_chat_id).chat["messages"][0]["content"] == "open message"

    def test_cross_user_dek_isolation(self):
        """User B's DEK cannot decrypt user A's ciphertext."""
        uid_a = str(uuid.uuid4())
        uid_b = str(uuid.uuid4())
        user_a = self._provision_encrypted_user(
            user_id=uid_a,
            email_hmac=f"hmac-{uid_a}",
            billing_id=f"billing-{uid_a}",
        )
        user_b = self._provision_encrypted_user(
            user_id=uid_b,
            email_hmac=f"hmac-{uid_b}",
            billing_id=f"billing-{uid_b}",
        )

        chat_id = self._create_chat_with_messages(
            user_a.id, [{"role": "user", "content": "user A secret"}]
        )

        # Get raw ciphertext
        raw_chat = self._get_raw_chat_json(chat_id)
        ciphertext = raw_chat["messages"][0]["content"]["ciphertext"]

        # Decrypt user B's DEK
        from open_webui.utils.encryption_utils import decrypt_dek, current_user_dek_context
        import base64

        dek_b = decrypt_dek(user_b.user_encrypted_dek, user_b.user_key)

        # Attempt decryption with user B's DEK — should fail
        token = current_user_dek_context.set(dek_b)
        try:
            f = Fernet(dek_b)
            raw_token = base64.urlsafe_b64decode(ciphertext.encode("utf-8"))
            try:
                f.decrypt(raw_token)
                assert False, "User B's DEK should NOT decrypt user A's ciphertext"
            except InvalidToken:
                pass  # Expected
        finally:
            current_user_dek_context.reset(token)

    def test_corrupted_ciphertext_graceful_degradation(self):
        """Corrupted ciphertext preserves encrypted dict on load — no crash."""
        uid = str(uuid.uuid4())
        user = self._provision_encrypted_user(
            user_id=uid,
            email_hmac=f"hmac-{uid}",
            billing_id=f"billing-{uid}",
        )

        chat_id = self._create_chat_with_messages(
            user.id, [{"role": "user", "content": "will be corrupted"}]
        )

        # Verify it's encrypted at rest
        raw_chat = self._get_raw_chat_json(chat_id)
        assert raw_chat["messages"][0]["content"]["is_encrypted"] is True

        # Corrupt the ciphertext via raw SQL
        from open_webui.internal.db import get_db
        from sqlalchemy import text

        corrupted_content = {"ciphertext": "AAAA-corrupted-garbage", "is_encrypted": True}
        corrupted_chat = dict(raw_chat)
        corrupted_chat["messages"] = [{"role": "user", "content": corrupted_content}]

        with get_db() as db:
            db.execute(
                text("UPDATE chat SET chat = :chat_json WHERE id = :id"),
                {"chat_json": json.dumps(corrupted_chat), "id": chat_id},
            )
            db.commit()

        # ORM load should NOT crash — should preserve the encrypted dict
        from open_webui.models.chats import Chats

        # Expire cached instances so we get a fresh load from DB
        from open_webui.internal.db import Session

        Session.expire_all()

        loaded = Chats.get_chat_by_id(chat_id)
        assert loaded is not None
        msg_content = loaded.chat["messages"][0]["content"]
        # Decryption failed, so content should still be the encrypted dict
        assert isinstance(msg_content, dict)
        assert msg_content.get("is_encrypted") is True

    def test_chat_update_reencrypts(self):
        """Updating a chat re-encrypts the updated content."""
        uid = str(uuid.uuid4())
        user = self._provision_encrypted_user(
            user_id=uid,
            email_hmac=f"hmac-{uid}",
            billing_id=f"billing-{uid}",
        )

        chat_id = self._create_chat_with_messages(
            user.id, [{"role": "user", "content": "original message"}]
        )

        # Verify initial encryption
        raw_before = self._get_raw_chat_json(chat_id)
        assert raw_before["messages"][0]["content"]["is_encrypted"] is True
        ciphertext_before = raw_before["messages"][0]["content"]["ciphertext"]

        # Update chat
        from open_webui.models.chats import Chats

        updated_chat_data = {
            "name": "test-chat",
            "messages": [{"role": "user", "content": "updated message"}],
        }
        Chats.update_chat_by_id(chat_id, updated_chat_data)

        # Raw SQL: updated content should also be encrypted
        raw_after = self._get_raw_chat_json(chat_id)
        assert raw_after["messages"][0]["content"]["is_encrypted"] is True
        ciphertext_after = raw_after["messages"][0]["content"]["ciphertext"]
        assert ciphertext_after != ciphertext_before, "Ciphertext should change after update"

        # ORM read: should return updated plaintext
        loaded = Chats.get_chat_by_id(chat_id)
        assert loaded.chat["messages"][0]["content"] == "updated message"

    def test_api_roundtrip_encrypted_chat(self):
        """Full FastAPI path: POST/GET returns plaintext while DB has ciphertext."""
        uid = str(uuid.uuid4())
        user = self._provision_encrypted_user(
            user_id=uid,
            email_hmac=f"hmac-{uid}",
            billing_id=f"billing-{uid}",
        )

        # Create chat via API
        with mock_webui_user(id=user.id):
            response = self.fast_api_client.post(
                self.create_url("/new"),
                json={
                    "chat": {
                        "name": "api-test-chat",
                        "messages": [
                            {"role": "user", "content": "API encrypted message"}
                        ],
                    }
                },
            )
        assert response.status_code == 200
        data = response.json()
        chat_id = data["id"]

        # API response should contain plaintext
        assert data["chat"]["messages"][0]["content"] == "API encrypted message"

        # Raw SQL should contain ciphertext
        raw_chat = self._get_raw_chat_json(chat_id)
        content_at_rest = raw_chat["messages"][0]["content"]
        assert isinstance(content_at_rest, dict)
        assert content_at_rest["is_encrypted"] is True

        # GET via API should also return plaintext
        with mock_webui_user(id=user.id):
            get_response = self.fast_api_client.get(self.create_url(f"/{chat_id}"))
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["chat"]["messages"][0]["content"] == "API encrypted message"

    # ── New Tests: Security ──────────────────────────────────────────────

    def test_api_idor_encrypted_chat(self):
        """User B cannot read user A's chat via API (HTTP 401, no content leak)."""
        uid_a = str(uuid.uuid4())
        uid_b = str(uuid.uuid4())
        user_a = self._provision_encrypted_user(
            user_id=uid_a,
            email_hmac=f"hmac-{uid_a}",
            billing_id=f"billing-{uid_a}",
        )
        user_b = self._provision_encrypted_user(
            user_id=uid_b,
            email_hmac=f"hmac-{uid_b}",
            billing_id=f"billing-{uid_b}",
        )

        # User A creates a chat
        chat_id = self._create_chat_with_messages(
            user_a.id, [{"role": "user", "content": "A's secret message"}]
        )

        # User B tries to read it via API
        with mock_webui_user(id=user_b.id):
            response = self.fast_api_client.get(self.create_url(f"/{chat_id}"))
        assert response.status_code == 401
        assert "chat" not in response.json()

    def test_concurrent_dek_isolation(self):
        """Two threads with different DEKs never see each other's key (ContextVar isolation)."""
        uid_a = str(uuid.uuid4())
        uid_b = str(uuid.uuid4())
        user_a = self._provision_encrypted_user(
            user_id=uid_a,
            email_hmac=f"hmac-{uid_a}",
            billing_id=f"billing-{uid_a}",
        )
        user_b = self._provision_encrypted_user(
            user_id=uid_b,
            email_hmac=f"hmac-{uid_b}",
            billing_id=f"billing-{uid_b}",
        )

        from open_webui.utils.encryption_utils import decrypt_dek, current_user_dek_context

        dek_a = decrypt_dek(user_a.user_encrypted_dek, user_a.user_key)
        dek_b = decrypt_dek(user_b.user_encrypted_dek, user_b.user_key)

        results = {}

        def thread_fn(name, dek):
            token = current_user_dek_context.set(dek)
            try:
                time.sleep(0.05)  # Brief sleep to interleave threads
                observed = current_user_dek_context.get()
                results[name] = observed
            finally:
                current_user_dek_context.reset(token)

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_a = pool.submit(thread_fn, "a", dek_a)
            fut_b = pool.submit(thread_fn, "b", dek_b)
            fut_a.result()
            fut_b.result()

        assert results["a"] == dek_a, "Thread A saw wrong DEK"
        assert results["b"] == dek_b, "Thread B saw wrong DEK"

    def test_chat_list_returns_decrypted_content(self):
        """Bulk chat list returns decrypted plaintext, not encrypted dicts."""
        uid = str(uuid.uuid4())
        user = self._provision_encrypted_user(
            user_id=uid,
            email_hmac=f"hmac-{uid}",
            billing_id=f"billing-{uid}",
        )

        self._create_chat_with_messages(
            user.id, [{"role": "user", "content": "first chat message"}]
        )
        self._create_chat_with_messages(
            user.id, [{"role": "user", "content": "second chat message"}]
        )

        from open_webui.models.chats import Chats

        chats = Chats.get_chats_by_user_id(user.id)
        assert len(chats) == 2
        contents = [c.chat["messages"][0]["content"] for c in chats]
        assert "first chat message" in contents
        assert "second chat message" in contents
        # Ensure none are encrypted dicts
        for c in chats:
            assert isinstance(c.chat["messages"][0]["content"], str)

    # ── New Tests: Data Correctness ──────────────────────────────────────

    def test_multi_message_multi_role_chat(self):
        """All 4 messages (user/assistant alternating) encrypted at rest, decrypted on read."""
        uid = str(uuid.uuid4())
        user = self._provision_encrypted_user(
            user_id=uid,
            email_hmac=f"hmac-{uid}",
            billing_id=f"billing-{uid}",
        )

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "I'm doing well."},
        ]
        chat_id = self._create_chat_with_messages(user.id, messages)

        # Raw SQL: all 4 contents should be encrypted dicts
        raw_chat = self._get_raw_chat_json(chat_id)
        raw_messages = raw_chat["messages"]
        assert len(raw_messages) == 4
        for msg in raw_messages:
            assert isinstance(msg["content"], dict)
            assert msg["content"]["is_encrypted"] is True

        # ORM read: all 4 should match original plaintext
        from open_webui.models.chats import Chats

        loaded = Chats.get_chat_by_id(chat_id)
        loaded_messages = loaded.chat["messages"]
        assert len(loaded_messages) == 4
        for original, loaded_msg in zip(messages, loaded_messages):
            assert loaded_msg["content"] == original["content"]

    def test_unicode_emoji_content_roundtrip(self):
        """UTF-8 content survives Fernet encrypt/decrypt without corruption."""
        uid = str(uuid.uuid4())
        user = self._provision_encrypted_user(
            user_id=uid,
            email_hmac=f"hmac-{uid}",
            billing_id=f"billing-{uid}",
        )

        unicode_content = "Hello \U0001f510 \u4e16\u754c \u0442\u0435\u0441\u0442"
        chat_id = self._create_chat_with_messages(
            user.id, [{"role": "user", "content": unicode_content}]
        )

        # Raw SQL: encrypted dict
        raw_chat = self._get_raw_chat_json(chat_id)
        assert isinstance(raw_chat["messages"][0]["content"], dict)
        assert raw_chat["messages"][0]["content"]["is_encrypted"] is True

        # ORM read: exact string match
        from open_webui.models.chats import Chats

        loaded = Chats.get_chat_by_id(chat_id)
        assert loaded.chat["messages"][0]["content"] == unicode_content

    def test_empty_and_none_content_edge_cases(self):
        """Empty string and None content pass through without encryption or crash."""
        uid = str(uuid.uuid4())
        user = self._provision_encrypted_user(
            user_id=uid,
            email_hmac=f"hmac-{uid}",
            billing_id=f"billing-{uid}",
        )

        # Sub-case 1: empty string
        chat_id_empty = self._create_chat_with_messages(
            user.id, [{"role": "user", "content": ""}]
        )
        raw_empty = self._get_raw_chat_json(chat_id_empty)
        assert raw_empty["messages"][0]["content"] == ""

        from open_webui.models.chats import Chats

        loaded_empty = Chats.get_chat_by_id(chat_id_empty)
        assert loaded_empty.chat["messages"][0]["content"] == ""

        # Sub-case 2: None content
        chat_id_none = self._create_chat_with_messages(
            user.id, [{"role": "user", "content": None}]
        )
        raw_none = self._get_raw_chat_json(chat_id_none)
        assert raw_none["messages"][0]["content"] is None

        loaded_none = Chats.get_chat_by_id(chat_id_none)
        assert loaded_none.chat["messages"][0]["content"] is None

    def test_multimodal_list_content_not_encrypted(self):
        """List-type content (multimodal messages) is stored as-is — documents known gap."""
        uid = str(uuid.uuid4())
        user = self._provision_encrypted_user(
            user_id=uid,
            email_hmac=f"hmac-{uid}",
            billing_id=f"billing-{uid}",
        )

        list_content = [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "url": "data:image/png;base64,AAAA"},
        ]
        chat_id = self._create_chat_with_messages(
            user.id, [{"role": "user", "content": list_content}]
        )

        # Raw SQL: content is the original list (not encrypted)
        raw_chat = self._get_raw_chat_json(chat_id)
        raw_content = raw_chat["messages"][0]["content"]
        assert isinstance(raw_content, list)
        assert raw_content == list_content

        # ORM read: same list returned
        from open_webui.models.chats import Chats

        loaded = Chats.get_chat_by_id(chat_id)
        assert loaded.chat["messages"][0]["content"] == list_content

    def test_chat_title_stored_as_plaintext(self):
        """Chat.title column is never encrypted — intentional for searchability."""
        uid = str(uuid.uuid4())
        user = self._provision_encrypted_user(
            user_id=uid,
            email_hmac=f"hmac-{uid}",
            billing_id=f"billing-{uid}",
        )

        from open_webui.models.chats import ChatForm, Chats

        chat = Chats.insert_new_chat(
            user.id,
            ChatForm(chat={
                "title": "My Secret Title",
                "name": "test",
                "messages": [{"role": "user", "content": "some content"}],
            }),
        )
        assert chat is not None

        # Raw SQL: title column is plain string
        from open_webui.internal.db import get_db
        from sqlalchemy import text

        with get_db() as db:
            row = db.execute(
                text("SELECT title FROM chat WHERE id = :id"),
                {"id": chat.id},
            ).fetchone()
            title = row[0]

        assert isinstance(title, str)
        assert title == "My Secret Title"
        assert "is_encrypted" not in str(title)

    def test_ciphertext_nondeterminism(self):
        """Same plaintext produces different ciphertext each time (Fernet fresh IV)."""
        uid = str(uuid.uuid4())
        user = self._provision_encrypted_user(
            user_id=uid,
            email_hmac=f"hmac-{uid}",
            billing_id=f"billing-{uid}",
        )

        from open_webui.utils.encryption_utils import (
            decrypt_dek,
            encrypt_message,
            decrypt_message,
            current_user_dek_context,
        )

        dek = decrypt_dek(user.user_encrypted_dek, user.user_key)
        token = current_user_dek_context.set(dek)
        try:
            ct1 = encrypt_message("same plaintext")
            ct2 = encrypt_message("same plaintext")
            assert ct1 != ct2, "Ciphertexts should differ (fresh IV per call)"
            assert decrypt_message(ct1) == "same plaintext"
            assert decrypt_message(ct2) == "same plaintext"
        finally:
            current_user_dek_context.reset(token)

    # ── New Tests: Operational Resilience ─────────────────────────────────

    def test_partial_provisioning_fallback(self):
        """User with no encryption keys stores/reads plaintext without error."""
        uid = str(uuid.uuid4())
        user = self._provision_plaintext_user(
            user_id=uid,
            email=f"{uid}@example.com",
        )

        chat_id = self._create_chat_with_messages(
            user.id, [{"role": "user", "content": "no encryption here"}]
        )

        # Raw SQL: plain string (not encrypted dict)
        raw_chat = self._get_raw_chat_json(chat_id)
        assert raw_chat["messages"][0]["content"] == "no encryption here"

        # ORM read: same plain string
        from open_webui.models.chats import Chats

        loaded = Chats.get_chat_by_id(chat_id)
        assert loaded.chat["messages"][0]["content"] == "no encryption here"

    def test_historical_plaintext_chat_survives_load(self):
        """Pre-encryption plaintext chats load correctly for encrypted users."""
        uid = str(uuid.uuid4())
        user = self._provision_encrypted_user(
            user_id=uid,
            email_hmac=f"hmac-{uid}",
            billing_id=f"billing-{uid}",
        )

        # Insert chat via raw SQL with plain string content (simulates pre-encryption data)
        from open_webui.internal.db import get_db
        from sqlalchemy import text

        chat_id = str(uuid.uuid4())
        chat_json = {"messages": [{"role": "user", "content": "legacy plain message"}]}
        ts = int(time.time())

        with get_db() as db:
            db.execute(
                text(
                    "INSERT INTO chat (id, user_id, title, chat, created_at, updated_at) "
                    "VALUES (:id, :uid, :title, :chat, :ts, :ts)"
                ),
                {
                    "id": chat_id,
                    "uid": user.id,
                    "title": "legacy",
                    "chat": json.dumps(chat_json),
                    "ts": ts,
                },
            )
            db.commit()

        # ORM read: plain string content should load without error
        from open_webui.models.chats import Chats

        loaded = Chats.get_chat_by_id(chat_id)
        assert loaded is not None
        assert loaded.chat["messages"][0]["content"] == "legacy plain message"
