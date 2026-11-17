import pytest
from fastapi.testclient import TestClient

from server import db
from server.main import create_app, dist_file
from tests.helpers import PDF, register, upload


def test_other_user_cannot_touch_file(app):
    with TestClient(app) as anna, TestClient(app) as boris:
        register(anna, "anna")
        register(boris, "boris")
        file_id = upload(anna, "secret.pdf").json()["file"]["id"]
        for path in (
            f"/api/files/{file_id}",
            f"/api/files/{file_id}/download",
            f"/api/files/{file_id}/preview",
        ):
            assert boris.get(path).status_code == 404
        assert boris.delete(f"/api/files/{file_id}").status_code == 404
        assert boris.post("/api/print", json={"file_id": file_id}).status_code == 404
        assert boris.get("/api/files").json()["files"] == []
        assert anna.get(f"/api/files/{file_id}/download").content.startswith(b"%PDF")


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("a.exe", b"MZ"),
        ("a.html", b"<html>"),
        ("a.svg", b"<svg></svg>"),
        ("a.pdf", b"not a pdf"),
        ("a.png", b"\xff\xd8\xffnotpng"),
        ("a.txt", b"\x00binary"),
        ("a.docx", b"%PDF-1.4\n"),
        ("a.doc", b"PK\x03\x04not-ole"),
    ],
)
def test_rejects_bad_upload(client, name, content):
    response = upload(client, name, content)
    assert response.status_code == 400


def test_rejected_upload_does_not_create_guest(app, client):
    assert upload(client, "a.exe", b"MZ").status_code == 400
    assert client.get("/api/auth/me").json()["user"] is None
    conn = db.connect(app.state.db_path)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    finally:
        conn.close()
    assert count == 0


def test_empty_and_too_big(tmp_path):
    app = create_app(tmp_path, max_upload_bytes=16)
    with TestClient(app) as client:
        assert upload(client, "a.pdf", b"").status_code == 400
        assert upload(client, "a.pdf", b"%PDF-1.4\n0123456789").status_code == 413


def test_filename_cannot_escape_storage(app, client):
    response = upload(client, "../../etc/passwd.pdf", PDF)
    assert response.status_code == 200
    assert response.json()["file"]["name"] == "passwd.pdf"
    conn = db.connect(app.state.db_path)
    try:
        relative = conn.execute("SELECT path FROM files").fetchone()["path"]
    finally:
        conn.close()
    assert "/" not in relative and ".." not in relative
    assert (app.state.storage_dir / relative).is_file()


def test_download_ignores_path_escape_in_database(app, client):
    file_id = upload(client).json()["file"]["id"]
    marker = app.state.storage_dir.parent / "secret.txt"
    marker.write_text("secret", encoding="utf-8")
    conn = db.connect(app.state.db_path)
    try:
        conn.execute("UPDATE files SET path = ? WHERE id = ?", ("../secret.txt", file_id))
        conn.commit()
    finally:
        conn.close()
    assert client.get(f"/api/files/{file_id}/download").status_code == 404
    assert client.delete(f"/api/files/{file_id}").status_code == 200
    assert marker.read_text(encoding="utf-8") == "secret"


def test_search_escapes_wildcards_and_sorts(client):
    register(client, "ivan")
    upload(client, "b.pdf")
    upload(client, "a.pdf")
    upload(client, "100%_x.pdf")
    found = client.get("/api/files", params={"q": "100%_x"}).json()["files"]
    assert [item["name"] for item in found] == ["100%_x.pdf"]
    percent = client.get("/api/files", params={"q": "%"}).json()["files"]
    assert [item["name"] for item in percent] == ["100%_x.pdf"]
    names = [
        item["name"]
        for item in client.get("/api/files", params={"sort": "name", "order": "asc"}).json()["files"]
    ]
    assert names == ["100%_x.pdf", "a.pdf", "b.pdf"]
    assert client.get("/api/files", params={"sort": "path"}).status_code == 400


def test_file_limit(app, client, monkeypatch):
    monkeypatch.setattr("server.files.MAX_FILES_PER_USER", 1)
    assert upload(client).status_code == 200
    assert upload(client, "b.pdf").status_code == 400


def test_delete_removes_disk_file(app, client):
    file_id = upload(client).json()["file"]["id"]
    conn = db.connect(app.state.db_path)
    try:
        relative = conn.execute("SELECT path FROM files WHERE id = ?", (file_id,)).fetchone()["path"]
    finally:
        conn.close()
    disk = app.state.storage_dir / relative
    preview_pdf = app.state.preview_dir / f"{file_id}.pdf"
    preview_jpg = app.state.preview_dir / f"{file_id}.jpg"
    preview_pdf.write_bytes(b"%PDF")
    preview_jpg.write_bytes(b"jpeg")
    assert disk.is_file()
    assert client.delete(f"/api/files/{file_id}").status_code == 200
    assert not disk.exists()
    assert not preview_pdf.exists()
    assert not preview_jpg.exists()
    assert client.get(f"/api/files/{file_id}").status_code == 404


def _picture(width, height, kind):
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, kind)
    return buf.getvalue()


def test_image_preview_is_small_jpeg(app, client):
    png = _picture(2400, 1800, "PNG")
    file_id = upload(client, "pic.png", png).json()["file"]["id"]
    first = client.get(f"/api/files/{file_id}/preview")
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("image/jpeg")
    assert first.content != png
    cached = app.state.preview_dir / f"{file_id}.jpg"
    assert cached.is_file()
    stamp = cached.stat().st_mtime_ns
    second = client.get(f"/api/files/{file_id}/preview")
    assert second.content == first.content
    assert cached.stat().st_mtime_ns == stamp
    downloaded = client.get(f"/api/files/{file_id}/download")
    assert downloaded.content == png
    assert downloaded.headers["content-type"].startswith("image/png")
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(first.content)) as preview:
        assert preview.size == (1600, 1200)


def test_jpeg_preview_shrinks_long_side(client):
    from io import BytesIO

    from PIL import Image
    from PIL.ExifTags import Base

    jpeg = _picture(3000, 1000, "JPEG")
    file_id = upload(client, "wide.jpg", jpeg).json()["file"]["id"]
    preview = client.get(f"/api/files/{file_id}/preview")
    assert preview.status_code == 200
    with Image.open(BytesIO(preview.content)) as image:
        assert max(image.size) == 1600
        assert image.size[0] > image.size[1]

    turned = Image.new("L", (40, 10), 80)
    exif = turned.getexif()
    exif[Base.Orientation] = 6
    buf = BytesIO()
    turned.save(buf, "JPEG", exif=exif)
    oriented_id = upload(client, "phone.jpg", buf.getvalue()).json()["file"]["id"]
    oriented = client.get(f"/api/files/{oriented_id}/preview")
    assert oriented.status_code == 200
    with Image.open(BytesIO(oriented.content)) as image:
        # Тег 6 — 90° по часовой: 40×10 становится 10×40.
        assert image.size == (10, 40)
    assert client.get(f"/api/files/{oriented_id}/download").content == buf.getvalue()


def test_tiff_preview_is_jpeg_not_pdf(client):
    tiff = _picture(32, 16, "TIFF")
    file_id = upload(client, "scan.tiff", tiff).json()["file"]["id"]
    preview = client.get(f"/api/files/{file_id}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/jpeg")
    assert preview.content.startswith(b"\xff\xd8\xff")
    assert client.get(f"/api/files/{file_id}/download").content == tiff


def test_preview_text_and_broken_image(client):
    text_id = upload(client, "a.txt", "Привет".encode()).json()["file"]["id"]
    preview = client.get(f"/api/files/{text_id}/preview")
    assert "Привет" in preview.text

    broken = upload(client, "pic.png", b"\x89PNG\r\n\x1a\nfake").json()["file"]["id"]
    assert client.get(f"/api/files/{broken}/preview").status_code == 503
    assert client.get(f"/api/files/{broken}/download").content.startswith(b"\x89PNG")


def test_office_preview_uses_converter(client, monkeypatch):
    def fake_convert(source, dest):
        assert source.suffix == ".docx"
        dest.write_bytes(b"%PDF-1.4\nconverted")

    monkeypatch.setattr("server.files.convert_to_pdf", fake_convert)
    file_id = upload(client, "note.docx", b"PK\x03\x04docx").json()["file"]["id"]
    preview = client.get(f"/api/files/{file_id}/preview")
    assert preview.status_code == 200
    assert preview.content.startswith(b"%PDF")


def test_dist_file_stays_inside_and_spa_does_not_swallow_api(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("home", encoding="utf-8")
    (dist / "app.js").write_text("js", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("no", encoding="utf-8")
    assert dist_file(dist, "index.html").read_text(encoding="utf-8") == "home"
    assert dist_file(dist, "../secret.txt") is None
    assert dist_file(dist, "app.js").read_text(encoding="utf-8") == "js"

    app = create_app(tmp_path / "data", dist_dir=dist)
    with TestClient(app) as client:
        assert client.get("/print").text == "home"
        assert client.get("/missing.js").status_code == 404
        assert client.get("/api/auth/me").json() == {"user": None}
        missing = client.get("/api/no-such")
        assert missing.status_code == 404
        assert "html" not in missing.headers.get("content-type", "")
