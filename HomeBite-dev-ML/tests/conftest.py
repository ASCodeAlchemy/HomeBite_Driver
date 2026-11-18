import os
import sys

# Must be set BEFORE the app modules are imported
os.environ["DATABASE_URL"] = "sqlite:///./test_dispatch.db"
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ.pop("MAPBOX_ACCESS_TOKEN", None)
os.environ["ASSIGNMENT_TIMEOUT_SECONDS"] = "20"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["RECO_DATA_DIR"] = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ml", "recommendation", "data")

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

import redis_client
from database import engine
from main import app


@pytest.fixture()
def client(monkeypatch):
    # Swap real Redis for an in-memory fake, and start every test with empty tables
    monkeypatch.setattr(redis_client, "r", fakeredis.FakeRedis(decode_responses=True))
    SQLModel.metadata.drop_all(engine)
    with TestClient(app) as c:  # entering the context runs startup -> create tables
        yield c
