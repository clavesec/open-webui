import pytest
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    JSON,
    event,
)
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON

# This import will fail, which is the goal of the "Red" phase of TDD
from open_webui.models.encryption_events import (
    register_encryption_listeners,
    EncryptionKeyMissingError,
)
from open_webui.services.encryption_service import dek_context, encrypt_content

# --- Test Setup: Create a minimal Chat model for testing ---
Base = declarative_base()


class Chat(Base):
    __tablename__ = "chat"
    id = Column(Integer, primary_key=True)
    chat = Column(SQLiteJSON)


# --- Tests ---


@pytest.fixture(scope="function")
def setup_db_with_listeners(db_session):
    """Fixture to set up the DB engine, create tables, and register listeners."""
    engine = db_session.get_bind()
    Base.metadata.create_all(engine)
    register_encryption_listeners()
    yield db_session
    event.remove(Chat, "before_insert", aname="encrypt_on_save")
    event.remove(Chat, "before_update", aname="encrypt_on_save")
    event.remove(Chat, "load", aname="decrypt_on_load")


def test_encrypt_on_save(setup_db_with_listeners):
    """
    Tests that the 'before_insert' event correctly encrypts chat content.
    """
    session = setup_db_with_listeners
    # 1. Arrange: Create a mock DEK and a plaintext chat object
    dek = b"a_valid_fernet_key_must_be_32_bytes_long_urlsafe"
    dek_context.set(dek)

    original_chat_data = {
        "history": [
            {"role": "user", "content": "This is a secret message."},
            {"role": "assistant", "content": "This is a secret response."},
        ]
    }
    new_chat = Chat(chat=original_chat_data)

    # 2. Act: Save the object to the database, triggering the event
    session.add(new_chat)
    session.commit()

    # 3. Assert: Query the raw data to ensure it's encrypted
    # We use a raw SQL query to bypass the `decrypt_on_load` listener.
    raw_result = session.execute(
        "SELECT chat FROM chat WHERE id = :id", {"id": new_chat.id}
    ).scalar_one()

    # The raw data should be a JSON string, not a Python dict
    import json

    raw_chat_data = json.loads(raw_result)

    assert isinstance(raw_chat_data["history"][0]["content"], dict)
    assert raw_chat_data["history"][0]["content"]["is_encrypted"] is True
    assert "ciphertext" in raw_chat_data["history"][0]["content"]
    assert raw_chat_data["history"][0]["content"]["ciphertext"] != "This is a secret message."


def test_decrypt_on_load(setup_db_with_listeners):
    """
    Tests that the 'load' event correctly decrypts chat content.
    """
    session = setup_db_with_listeners
    # 1. Arrange: Manually create and insert an encrypted record
    dek = b"another_valid_key_that_is_32_bytes_long_and_safe"
    dek_context.set(dek)

    encrypted_content = encrypt_content("The secret is out!", dek)
    encrypted_chat_data = {"history": [{"role": "user", "content": encrypted_content}]}

    chat_to_save = Chat(chat=encrypted_chat_data)
    session.add(chat_to_save)
    session.commit()
    chat_id = chat_to_save.id

    # Clear the session to ensure a fresh load from the DB
    session.expire_all()

    # 2. Act: Load the object using the ORM, triggering the 'load' event
    reloaded_chat = session.get(Chat, chat_id)

    # 3. Assert: The content should be decrypted back to plaintext
    assert reloaded_chat.chat["history"][0]["content"] == "The secret is out!"


def test_save_fails_without_dek(setup_db_with_listeners):
    """
    Tests that saving a chat without a DEK in the context raises an error.
    """
    session = setup_db_with_listeners
    dek_context.set(None)  # Ensure no key is present

    chat_to_fail = Chat(chat={"history": [{"role": "user", "content": "This should fail"}]})
    session.add(chat_to_fail)

    with pytest.raises(EncryptionKeyMissingError):
        session.commit()