# backend/open_webui/services/encryption_service.py

import os
import base64
import logging
from contextvars import ContextVar
from typing import Dict, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# ==============================================================================
# 1. CONTEXT VARIABLE
# This thread-safe variable will hold the active DEK for the current request.
# ==============================================================================
dek_context: ContextVar[Optional[bytes]] = ContextVar("dek_context", default=None)

log = logging.getLogger(__name__)

# --- Simple Prefix for Testing ---
ENCRYPTION_PREFIX = "12345 - "
# ---------------------------------

# ==============================================================================
class MockEncryptionService:
    """
    A mock service for testing the encryption/decryption pipeline.
    Uses a simple string prefix instead of real encryption.
    """
    def __init__(self):
        self._encrypted_deks: Dict[str, bytes] = {}
        self._salts: Dict[str, bytes] = {}
        log.warning("MockEncryptionService is active with TEST PREFIX logic.")

    def _derive_user_master_key(self, password: str, salt: bytes) -> bytes:
        """MOCK: Derives a key. In a real system, use a proper KDF."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))

    def register_user_and_create_dek(self, user_id: str, password: str):
        """MOCK: Simulates creating and storing a user's encrypted key."""
        if user_id in self._encrypted_deks:
            return
        salt = os.urandom(16)
        self._salts[user_id] = salt
        self._encrypted_deks[user_id] = b'mock-edek'
        log.info(f"Registered mock EDEK for user {user_id}.")

    def get_dek_for_session(self, user_id: str, password: str) -> Optional[bytes]:
        """MOCK: Returns a dummy 'key' if the user exists."""
        if user_id in self._encrypted_deks:
            log.info(f"Provided mock DEK for user {user_id} for this session.")
            return b'mock-dek-for-testing'
        return None

    def encrypt_content(self, plaintext: str, dek: bytes) -> Dict:
        """TEST ENCRYPTION: Adds a prefix and returns a structured object."""
        log.debug(f"TEST 'Encrypting': {plaintext[:30]}...")
        # We are NOT calling Fernet here for the test.
        return {
            "is_encrypted": True, 
            "ciphertext": f"{ENCRYPTION_PREFIX}{plaintext}"
        }

    def decrypt_content(self, encrypted_object: Dict, dek: bytes) -> str:
        """TEST DECRYPTION: Removes the prefix."""
        log.debug(f"TEST 'Decrypting': {encrypted_object.get('ciphertext', '')[:40]}...")
        ciphertext = encrypted_object.get("ciphertext", "")
        # if ciphertext.startswith(ENCRYPTION_PREFIX):
        #     return ciphertext[len(ENCRYPTION_PREFIX):]
        return ciphertext

# Create a single instance to be used by the application
encryption_service = MockEncryptionService()

