import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock
from cryptography.fernet import Fernet, InvalidToken

# This import will fail initially, which is the point of TDD's "Red" phase.
from open_webui.services.encryption_service import (
    register_user_and_create_dek,
    _derive_user_master_key, 
    get_dek_for_session,
    encrypt_content,
    decrypt_content,
    EncryptionKeyMissingError,
    dek_context,
)


class TestEncryptionService:
    """
    Unit tests for the core cryptographic functions in the EncryptionService.
    These tests do not require a database or live server.
    """

    @pytest.fixture
    def registered_user(self):
        """A pytest fixture to create a user with generated salt and EDEK."""
        # This part will fail until the implementation exists, which is intended.
        # We are defining the full test suite first.
        mock_user = SimpleNamespace(id="test_user_id", salt=None, encrypted_dek=None)
        password = "a_very_strong_password_123"
        register_user_and_create_dek(user=mock_user, password=password)
        # Add password to the object for convenience in tests
        mock_user.password = password
        return mock_user
    
    def test_register_user_and_create_dek_success(self):
        """
        Tests that a new user is correctly provisioned with an encryption
        salt and an encrypted Data Encryption Key (DEK).
        """
        # 1. Setup: Prepare the test inputs
        # We create a mock user object that behaves like a database model.
        # `SimpleNamespace` is a simple object that allows us to set attributes on it.
        mock_user = SimpleNamespace(id="test_user_id", salt=None, encrypted_dek=None)
        password = "a_very_strong_password_123"

        # 2. Act: Call the function we are testing
        # The spec says this function will modify the user object.
        register_user_and_create_dek(user=mock_user, password=password)

        # 3. Assert: Verify the results are correct
        # The user object should now have a salt and an encrypted DEK.
        assert mock_user.salt is not None, "Salt should be generated"
        assert isinstance(mock_user.salt, bytes), "Salt should be bytes"
        assert len(mock_user.salt) > 0, "Salt should not be empty"

        assert (
            mock_user.encrypted_dek is not None
        ), "Encrypted DEK should be generated"
        assert isinstance(
            mock_user.encrypted_dek, bytes
        ), "Encrypted DEK should be bytes"
        assert len(mock_user.encrypted_dek) > 0, "Encrypted DEK should not be empty"

        # A simple sanity check to ensure the two values are different
        assert mock_user.encrypted_dek != mock_user.salt

    def test_get_dek_for_session_success(self, registered_user):
        """
        Tests that a valid DEK can be retrieved using the correct password.
        """
        # Act
        dek = get_dek_for_session(user=registered_user, password=registered_user.password)

        # Assert
        assert isinstance(dek, bytes)
        # A Fernet key is a URL-safe base64-encoded 32-byte key.
        # The base64 encoding makes it 44 bytes long.
        assert len(dek) == 44

    def test_get_dek_for_session_wrong_password(self, registered_user):
        """
        Tests that retrieving a DEK with the wrong password raises an error.
        """
        # Act & Assert
        with pytest.raises(InvalidToken):
            get_dek_for_session(user=registered_user, password="this is the wrong password")

    def test_encryption_decryption_roundtrip(self, registered_user):
        """
        Tests that content can be encrypted and then decrypted back to the original plaintext.
        """
        # Arrange
        dek = get_dek_for_session(user=registered_user, password=registered_user.password)
        original_text = "This is a top secret message about llamas."

        # Act
        encrypted_obj = encrypt_content(original_text, dek)
        decrypted_text = decrypt_content(encrypted_obj, dek)

        # Assert
        assert isinstance(encrypted_obj, dict)
        assert encrypted_obj["is_encrypted"] is True
        assert "ciphertext" in encrypted_obj
        assert encrypted_obj["ciphertext"] != original_text
        assert decrypted_text == original_text

    def test_decrypt_with_wrong_dek_fails(self, registered_user):
        """
        Tests that attempting to decrypt content with a different DEK fails.
        """
        # Arrange
        dek1 = get_dek_for_session(user=registered_user, password=registered_user.password)
        dek2 = Fernet.generate_key()  # A different, valid key
        original_text = "This is another secret."
        encrypted_obj = encrypt_content(original_text, dek1)

        # Act & Assert
        with pytest.raises(InvalidToken):
            decrypt_content(encrypted_obj, dek2)