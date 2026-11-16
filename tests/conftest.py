import pytest
from fastapi.testclient import TestClient

from server.main import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
