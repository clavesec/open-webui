# services/encryption_service.py
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from typing import Optional

# This service class will be instantiated once per user session.
# It holds the user's decrypted Data Encryption Key (DEK) in memory
# for the duration of their session.
class EncryptionService:
    _dek: Optional[bytes] = None

    def __init__(self, user_key: bytes, user_encrypted_dek: bytes):
        """
        Initializes the service for a specific user's session.
        It immediately decrypts the user's DEK using their UserKey and holds
        the plaintext DEK in memory.

        Args:
            user_key: The user's 96-bit secret key, extracted from their
                      client certificate during the mTLS handshake.
            user_encrypted_dek: The user's encrypted DEK, retrieved from the
                                'users' table in the database.
        """
        if not user_key or not user_encrypted_dek:
            raise ValueError("User key and encrypted DEK cannot be empty.")

        # The encrypted DEK is stored as: nonce (12 bytes) + ciphertext + tag (16 bytes)
        nonce = user_encrypted_dek[:12]
        ciphertext_with_tag = user_encrypted_dek[12:]

        # Use AES-256-GCM to decrypt the DEK. The UserKey is used as the key.
        # Note: In a real implementation, the UserKey would need to be expanded
        # to 256 bits using a KDF if it's shorter (e.g., HKDF). For this example,
        # we assume the UserKey is already the correct length or padded.
        # For our 96-bit (12-byte) key, we'll need to derive a 256-bit (32-byte) key.
        # This is a placeholder for a real KDF implementation.
        if len(user_key) < 32:
             # In a real scenario, use HKDF or another standard KDF.
             # For this example, we'll pad it. This is NOT secure for production.
            aes_key = user_key.ljust(32, b'\0')
        else:
            aes_key = user_key

        aesgcm = AESGCM(aes_key)
        self._dek = aesgcm.decrypt(nonce, ciphertext_with_tag, None)

    def encrypt(self, plaintext: str) -> bytes:
        """
        Encrypts a plaintext string (e.g., a chat message) using the in-memory DEK.

        Args:
            plaintext: The string to encrypt.

        Returns:
            A byte string containing: nonce (12 bytes) + ciphertext + tag (16 bytes).
        """
        if self._dek is None:
            raise Exception("DEK is not available. Service not initialized correctly.")

        aesgcm = AESGCM(self._dek)
        nonce = os.urandom(12)  # GCM standard nonce size
        plaintext_bytes = plaintext.encode('utf-8')
        ciphertext = aesgcm.encrypt(nonce, plaintext_bytes, None)

        # Prepend the nonce to the ciphertext for storage
        return nonce + ciphertext

    def decrypt(self, encrypted_content: bytes) -> str:
        """
        Decrypts content using the in-memory DEK.

        Args:
            encrypted_content: The byte string containing the nonce + ciphertext + tag.

        Returns:
            The decrypted plaintext string.
        """
        if self._dek is None:
            raise Exception("DEK is not available. Service not initialized correctly.")
        
        if not encrypted_content or len(encrypted_content) < 13:
            # Handle empty or invalid content gracefully
            return ""

        nonce = encrypted_content[:12]
        ciphertext_with_tag = encrypted_content[12:]

        aesgcm = AESGCM(self._dek)
        
        try:
            decrypted_bytes = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
            return decrypted_bytes.decode('utf-8')
        except Exception as e:
            # Log the error and handle decryption failure gracefully
            print(f"Decryption failed: {e}")
            return "[DECRYPTION FAILED]"
