import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.base import Base
from app.core.config import settings
# Import the model package so every table (including newer ones like skills)
# is registered on Base.metadata before create_all runs.
import app.db  # noqa: F401

# Use an in-memory database for testing
TEST_DB_URL = "sqlite:///:memory:"

@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def db_session(db_engine):
    """
    Creates a fresh database session for a test.
    Rolls back any changes after the test completes.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    session.close()
    transaction.rollback()
    connection.close()

def pytest_addoption(parser):
    parser.addoption(
        "--mode", action="store", default="mock", help="Test mode: mock or real"
    )

@pytest.fixture(scope="session")
def test_mode(request):
    return request.config.getoption("--mode")
