from fastapi.testclient import TestClient

from server import db
from tests.helpers import PASSWORD, register, upload


def test_register_and_login_by_nickname(client):
    user = register(client, "Иван")
    assert user["nickname"] == "Иван"
    assert user["temporary"] is False
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["user"] is None

    response = client.post("/api/auth/login", json={"login": "Иван", "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["user"]["nickname"] == "Иван"
    assert "httponly" in response.headers["set-cookie"].lower()


def test_login_by_email_ignores_case(client):
    register(client, "ivan", "Ivan@Example.com")
    client.post("/api/auth/logout")
    response = client.post(
        "/api/auth/login",
        json={"login": "ivan@example.com", "password": PASSWORD},
    )
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "ivan@example.com"


def test_register_with_only_email(client):
    user = register(client, email="a@b.c")
    assert user["nickname"] is None
    assert user["email"] == "a@b.c"


def test_wrong_password_and_unknown_user_look_the_same(client):
    register(client, "ivan")
    client.post("/api/auth/logout")
    wrong = client.post("/api/auth/login", json={"login": "ivan", "password": "not-the-password"})
    missing = client.post("/api/auth/login", json={"login": "other", "password": "not-the-password"})
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == missing.json()["detail"]


def test_duplicate_nickname_and_short_password(client):
    register(client, "ivan")
    client.post("/api/auth/logout")
    again = client.post("/api/auth/register", json={"nickname": "ivan", "password": PASSWORD})
    assert again.status_code == 400
    assert "ник" in again.json()["detail"].lower()
    short = client.post("/api/auth/register", json={"nickname": "petr", "password": "short"})
    assert short.status_code == 400
    assert "8" in short.json()["detail"]


def test_register_needs_nickname_or_email(client):
    response = client.post("/api/auth/register", json={"password": PASSWORD})
    assert response.status_code == 400


def test_guest_appears_on_upload_and_register_keeps_files(client):
    assert client.get("/api/auth/me").json()["user"] is None
    uploaded = upload(client, "отчёт.pdf")
    assert uploaded.status_code == 200
    assert uploaded.json()["file"]["name"] == "отчёт.pdf"
    guest = client.get("/api/auth/me").json()["user"]
    assert guest["temporary"] is True
    file_id = uploaded.json()["file"]["id"]

    register(client, "ivan", "ivan@example.com")
    files = client.get("/api/files").json()["files"]
    assert [item["id"] for item in files] == [file_id]
    assert client.get("/api/auth/me").json()["user"]["temporary"] is False


def test_login_adopts_guest_files_and_drops_guest(app):
    with TestClient(app) as guest, TestClient(app) as owner:
        register(owner, "ivan")
        file_id = upload(guest).json()["file"]["id"]
        response = guest.post("/api/auth/login", json={"login": "ivan", "password": PASSWORD})
        assert response.status_code == 200
        assert response.json()["user"]["temporary"] is False
        assert file_id in [item["id"] for item in guest.get("/api/files").json()["files"]]
        assert file_id in [item["id"] for item in owner.get("/api/files").json()["files"]]
        conn = db.connect(app.state.db_path)
        try:
            left = conn.execute("SELECT COUNT(*) AS n FROM users WHERE is_temporary = 1").fetchone()["n"]
        finally:
            conn.close()
        assert left == 0


def test_adopt_does_not_take_other_files(app):
    with TestClient(app) as guest, TestClient(app) as other:
        other_id = upload(other).json()["file"]["id"]
        own_id = upload(guest).json()["file"]["id"]
        register(guest, "ivan")
        assert [item["id"] for item in guest.get("/api/files").json()["files"]] == [own_id]
        assert [item["id"] for item in other.get("/api/files").json()["files"]] == [other_id]


def test_already_logged_in_cannot_register_again(client):
    register(client, "ivan")
    response = client.post("/api/auth/register", json={"nickname": "petr", "password": PASSWORD})
    assert response.status_code == 400
