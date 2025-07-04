import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="function")
def db_session():
    """
    Provides a temporary, in-memory SQLite database session for tests.
    This allows testing of ORM events without a live database.
    """
    # Use in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine)
    session = Session()

    yield session

    session.close()
    engine.dispose()