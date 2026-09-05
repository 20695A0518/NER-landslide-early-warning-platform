"""Test fixtures: an isolated in-memory database and a seeded API client."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app


@pytest.fixture(scope="function")
def db_session():
    """A fresh in-memory database per test.

    StaticPool keeps every connection pointed at the same in-memory database -
    without it, SQLAlchemy hands out a new empty database per connection and
    the tables created here vanish before the request handler sees them.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture(scope="function")
def client(db_session):
    """API client bound to the isolated database, with the lifespan skipped.

    The real lifespan seeds, scores and starts a scheduler; running it per test
    would be slow and would leave background jobs mutating the database
    mid-assertion.
    """
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    # `with TestClient(app)` would run the lifespan; constructing it directly
    # does not.
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def seeded(db_session):
    """A database seeded with zones, roads, sensors and users (no history)."""
    from app.services.seed import seed

    seed(db_session, include_history=False)
    return db_session


@pytest.fixture
def admin_token(client, seeded):
    response = client.post(
        "/api/v1/auth/login", data={"username": "admin", "password": "admin123"}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}
