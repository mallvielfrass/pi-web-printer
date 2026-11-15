from server import scanner
from tests.helpers import upload


def test_scan_saves_user_file(app, client):
    def fake(dest, **kwargs):
        assert kwargs["resolution"] == 150
        assert kwargs["mode"] == "Gray"
        assert kwargs["image_format"] == "png"
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")

    app.state.scan_to_file = fake
    response = client.post("/api/scan", json={"resolution": 150, "mode": "gray", "format": "png"})
    assert response.status_code == 200
    saved = response.json()["file"]
    assert saved["kind"] == "scan"
    assert saved["name"].endswith(".png")
    listed = client.get("/api/files", params={"kind": "scan"}).json()["files"]
    assert [item["id"] for item in listed] == [saved["id"]]
    assert client.get("/api/auth/me").json()["user"]["temporary"] is True


def test_bad_resolution_does_not_create_guest(client):
    response = client.post("/api/scan", json={"resolution": 50, "mode": "gray", "format": "png"})
    assert response.status_code == 400
    assert client.get("/api/auth/me").json()["user"] is None


def test_scan_error_leaves_no_file(app, client):
    def fake(dest, **kwargs):
        raise scanner.ScannerError("нет устройства")

    app.state.scan_to_file = fake
    response = client.post("/api/scan", json={"resolution": 300, "mode": "color", "format": "pdf"})
    assert response.status_code == 503
    assert "нет устройства" in response.json()["detail"]
    assert client.get("/api/files").json()["files"] == []


def test_oversized_scan_is_deleted(app, client, monkeypatch):
    monkeypatch.setattr(scanner, "MAX_SCAN_BYTES", 4)

    def fake(dest, **kwargs):
        dest.write_bytes(b"12345")

    app.state.scan_to_file = fake
    response = client.post("/api/scan", json={})
    assert response.status_code == 400
    assert list(app.state.storage_dir.iterdir()) == []


def test_scan_does_not_replace_an_uploaded_file(app, client):
    uploaded = upload(client).json()["file"]["id"]

    def fake(dest, **kwargs):
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")

    app.state.scan_to_file = fake
    scanned = client.post("/api/scan", json={}).json()["file"]["id"]
    kinds = {item["id"]: item["kind"] for item in client.get("/api/files").json()["files"]}
    assert kinds[uploaded] == "print"
    assert kinds[scanned] == "scan"
