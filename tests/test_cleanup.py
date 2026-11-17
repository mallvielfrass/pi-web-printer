from fastapi.testclient import TestClient

from server import cleanup, db
from tests.helpers import register, upload


def test_expired_guest_loses_access_before_cleanup(app, client):
    file_id = upload(client).json()["file"]["id"]
    conn = db.connect(app.state.db_path)
    try:
        conn.execute(
            "UPDATE users SET expires_at = ? WHERE is_temporary = 1",
            ("2000-01-01T00:00:00+00:00",),
        )
        conn.commit()
    finally:
        conn.close()
    assert client.get("/api/auth/me").json()["user"] is None
    assert client.get(f"/api/files/{file_id}/download").status_code == 404


def test_cleanup_removes_expired_guest_only(app):
    with TestClient(app) as guest, TestClient(app) as other_guest, TestClient(app) as person:
        guest_file = upload(guest).json()["file"]
        other_file = upload(other_guest).json()["file"]
        register(person, "anna")
        own_file = upload(person, "own.pdf").json()["file"]

        conn = db.connect(app.state.db_path)
        try:
            owner = conn.execute(
                "SELECT owner_id, path FROM files WHERE id = ?",
                (guest_file["id"],),
            ).fetchone()
            conn.execute(
                "UPDATE users SET expires_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", owner["owner_id"]),
            )
            conn.commit()
        finally:
            conn.close()

        disk = app.state.storage_dir / owner["path"]
        preview_pdf = app.state.preview_dir / f"{guest_file['id']}.pdf"
        preview_jpg = app.state.preview_dir / f"{guest_file['id']}.jpg"
        preview_pdf.write_bytes(b"pdf")
        preview_jpg.write_bytes(b"jpg")
        assert disk.is_file()
        conn = db.connect(app.state.db_path)
        try:
            removed = cleanup.delete_expired(conn, app.state.storage_dir, app.state.preview_dir)
            gone_user = conn.execute("SELECT 1 FROM users WHERE id = ?", (owner["owner_id"],)).fetchone()
            gone_file = conn.execute("SELECT 1 FROM files WHERE id = ?", (guest_file["id"],)).fetchone()
            gone_session = conn.execute(
                "SELECT 1 FROM sessions WHERE user_id = ?",
                (owner["owner_id"],),
            ).fetchone()
            other_left = conn.execute("SELECT 1 FROM files WHERE id = ?", (other_file["id"],)).fetchone()
            own_left = conn.execute("SELECT 1 FROM files WHERE id = ?", (own_file["id"],)).fetchone()
            kinds = [row["is_temporary"] for row in conn.execute("SELECT is_temporary FROM users")]
        finally:
            conn.close()

        assert removed == 1
        assert not disk.exists()
        assert not preview_pdf.exists()
        assert not preview_jpg.exists()
        assert gone_user is None
        assert gone_file is None
        assert gone_session is None
        assert other_left is not None
        assert own_left is not None
        assert 0 in kinds
        assert 1 in kinds


def test_cleanup_drops_expired_session_and_keeps_user(app, client):
    register(client, "anna")
    conn = db.connect(app.state.db_path)
    try:
        conn.execute("UPDATE sessions SET expires_at = ?", ("2000-01-01T00:00:00+00:00",))
        conn.commit()
        removed = cleanup.delete_expired(conn, app.state.storage_dir, app.state.preview_dir)
        users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        sessions = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    finally:
        conn.close()
    assert removed == 0
    assert users == 1
    assert sessions == 0
