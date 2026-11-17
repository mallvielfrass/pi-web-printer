from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server import scanner
from server.main import app_from_env, create_app
from tests.helpers import upload


def test_scan_saves_user_file(app, client):
    def fake(dest, **kwargs):
        assert kwargs["resolution"] == 150
        assert kwargs["mode"] == "Gray"
        assert kwargs["image_format"] == "png"
        assert kwargs["geometry"] == "A4"
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


def test_resolution_1200_is_allowed(app, client):
    def fake(dest, **kwargs):
        assert kwargs["resolution"] == 1200
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")

    app.state.scan_to_file = fake
    response = client.post("/api/scan", json={"resolution": 1200, "mode": "color", "format": "png"})
    assert response.status_code == 200
    assert response.json()["file"]["name"].endswith(".png")


def test_geometry_full_size_is_passed(app, client):
    def fake(dest, **kwargs):
        assert kwargs["geometry"] == "Full size"
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")

    app.state.scan_to_file = fake
    response = client.post("/api/scan", json={"geometry": "Full size"})
    assert response.status_code == 200


def test_bad_geometry_does_not_create_guest(client):
    response = client.post("/api/scan", json={"geometry": "稿台"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Некорректная область"
    assert client.get("/api/auth/me").json()["user"] is None


def test_scan_command_passes_geometry_as_argument(tmp_path, monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        assert "shell" not in kwargs
        seen["command"] = list(command)

        class Proc:
            returncode = 0
            stderr = b""

        return Proc()

    monkeypatch.setattr(scanner.subprocess, "run", fake_run)
    dest = tmp_path / "out.png"
    scanner.scan_to_file(
        dest,
        resolution=300,
        mode="Gray",
        image_format="png",
        device="escl:http://127.0.0.1:60000",
        geometry="Full size",
    )
    assert seen["command"] == [
        "scanimage",
        "--resolution=300",
        "--mode=Gray",
        "--format=png",
        "--geometry",
        "Full size",
        f"--output-file={dest}",
        "--device-name=escl:http://127.0.0.1:60000",
    ]


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
    response = client.post("/api/scan", json={"resolution": 1200, "mode": "color", "format": "png"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Слишком большое изображение, уменьшите разрешение"
    assert list(app.state.storage_dir.iterdir()) == []


@pytest.mark.parametrize("fmt", ["png", "jpeg", "tiff"])
def test_color_1200_image_is_not_rotated(tmp_path, monkeypatch, fmt):
    app = create_app(tmp_path, scan_rotate=180)

    def fake(dest, **kwargs):
        dest.write_bytes(b"scan")

    def forbidden(path, degrees):
        raise AssertionError("Pillow не должен открывать цветной кадр 1200 dpi")

    app.state.scan_to_file = fake
    monkeypatch.setattr(scanner, "_rotate_image", forbidden)
    with TestClient(app) as client:
        response = client.post(
            "/api/scan",
            json={"resolution": 1200, "mode": "color", "format": fmt},
        )
    assert response.status_code == 400
    assert response.json()["detail"] == scanner.HEAVY_ROTATE_DETAIL
    assert list(app.state.storage_dir.iterdir()) == []


def test_color_1200_pdf_still_rotates(tmp_path):
    from pypdf import PdfReader, PdfWriter

    app = create_app(tmp_path, scan_rotate=180)

    def fake(dest, **kwargs):
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=100)
        with dest.open("wb") as handle:
            writer.write(handle)

    app.state.scan_to_file = fake
    with TestClient(app) as client:
        response = client.post(
            "/api/scan",
            json={"resolution": 1200, "mode": "color", "format": "pdf"},
        )
        assert response.status_code == 200, response.text
        data = client.get(f"/api/files/{response.json()['file']['id']}/download").content
    reader = PdfReader(BytesIO(data))
    assert [page.rotation for page in reader.pages] == [180]


def test_gray_1200_png_still_rotates(tmp_path):
    app = create_app(tmp_path, scan_rotate=90)

    def fake(dest, **kwargs):
        _marked_image(dest, "PNG")

    app.state.scan_to_file = fake
    with TestClient(app) as client:
        response = client.post(
            "/api/scan",
            json={"resolution": 1200, "mode": "gray", "format": "png"},
        )
        assert response.status_code == 200, response.text
        data = client.get(f"/api/files/{response.json()['file']['id']}/download").content
    with Image.open(BytesIO(data)) as image:
        assert image.size == (2, 3)


def _marked_image(path, kind):
    image = Image.new("RGB", (3, 2), (0, 0, 0))
    image.putpixel((0, 0), (255, 0, 0))
    image.save(path, kind)


@pytest.mark.parametrize(
    ("degrees", "size", "pixel"),
    [
        (90, (2, 3), (1, 0)),
        (180, (3, 2), (2, 1)),
        (270, (2, 3), (0, 2)),
    ],
)
def test_scan_rotate_png(tmp_path, degrees, size, pixel):
    app = create_app(tmp_path, scan_rotate=degrees)

    def fake(dest, **kwargs):
        _marked_image(dest, "PNG")

    app.state.scan_to_file = fake
    with TestClient(app) as client:
        response = client.post("/api/scan", json={"format": "png", "resolution": 150})
        assert response.status_code == 200, response.text
        data = client.get(f"/api/files/{response.json()['file']['id']}/download").content
    with Image.open(BytesIO(data)) as image:
        assert image.size == size
        assert image.getpixel(pixel) == (255, 0, 0)
        assert image.getpixel((0, 0)) != (255, 0, 0)


def test_scan_rotate_jpeg_keeps_format(tmp_path):
    app = create_app(tmp_path, scan_rotate=90)

    def fake(dest, **kwargs):
        _marked_image(dest, "JPEG")

    app.state.scan_to_file = fake
    with TestClient(app) as client:
        response = client.post("/api/scan", json={"format": "jpeg"})
        assert response.status_code == 200, response.text
        data = client.get(f"/api/files/{response.json()['file']['id']}/download").content
    with Image.open(BytesIO(data)) as image:
        assert image.format == "JPEG"
        assert image.size == (2, 3)


def test_scan_rotate_pdf_pages(tmp_path):
    from pypdf import PdfReader, PdfWriter

    app = create_app(tmp_path, scan_rotate=180)

    def fake(dest, **kwargs):
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=100)
        writer.add_blank_page(width=150, height=80)
        with dest.open("wb") as handle:
            writer.write(handle)

    app.state.scan_to_file = fake
    with TestClient(app) as client:
        response = client.post("/api/scan", json={"format": "pdf"})
        assert response.status_code == 200, response.text
        data = client.get(f"/api/files/{response.json()['file']['id']}/download").content
    reader = PdfReader(BytesIO(data))
    assert [page.rotation for page in reader.pages] == [180, 180]


def test_scan_without_rotation_keeps_scanner_bytes(app, client):
    payload = b"\x89PNG\r\n\x1a\nunchanged"

    def fake(dest, **kwargs):
        dest.write_bytes(payload)

    app.state.scan_to_file = fake
    response = client.post("/api/scan", json={"format": "png"})
    assert response.status_code == 200
    file_id = response.json()["file"]["id"]
    assert client.get(f"/api/files/{file_id}/download").content == payload


def test_rotate_error_deletes_scan(app, client, monkeypatch):
    app.state.scan_rotate = 180

    def fake(dest, **kwargs):
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")

    def boom(path, degrees):
        raise scanner.ScannerError("не повернули")

    app.state.scan_to_file = fake
    monkeypatch.setattr(scanner, "rotate_scan", boom)
    response = client.post("/api/scan", json={"format": "png"})
    assert response.status_code == 503
    assert "не повернули" in response.json()["detail"]
    assert list(app.state.storage_dir.iterdir()) == []


def test_bad_scan_rotate_rejected(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        create_app(tmp_path / "bad", scan_rotate=45)
    assert scanner.parse_scan_rotate(None) == 0
    assert scanner.parse_scan_rotate("  ") == 0
    assert scanner.parse_scan_rotate("180") == 180
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "env"))
    monkeypatch.setenv("SCAN_ROTATE", "15")
    with pytest.raises(SystemExit):
        app_from_env()


def test_scan_does_not_replace_an_uploaded_file(app, client):
    uploaded = upload(client).json()["file"]["id"]

    def fake(dest, **kwargs):
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")

    app.state.scan_to_file = fake
    scanned = client.post("/api/scan", json={}).json()["file"]["id"]
    kinds = {item["id"]: item["kind"] for item in client.get("/api/files").json()["files"]}
    assert kinds[uploaded] == "print"
    assert kinds[scanned] == "scan"
