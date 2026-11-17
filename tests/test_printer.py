from fastapi.testclient import TestClient

from server.printer import PrinterError, describe, job_view, paper_text, toner_text
from tests.helpers import upload


class FakeCups:
    def __init__(self, duplex=True, reasons=None, levels=(50,), highs=(100,)):
        self.jobs = []
        self.reasons = ["none"] if reasons is None else reasons
        self.levels = list(levels)
        self.highs = list(highs)
        self.duplex = duplex

    def getPrinters(self):
        return {"Pantum-M6507": {}}

    def getPrinterAttributes(self, name):
        assert name == "Pantum-M6507"
        sides = ["one-sided"]
        if self.duplex:
            sides.append("two-sided-long-edge")
        return {
            "printer-state": 3,
            "printer-state-reasons": self.reasons,
            "marker-levels": self.levels,
            "marker-high-levels": self.highs,
            "sides-supported": sides,
            "media-supported": ["A4", "A5"],
        }

    def printFile(self, printer, filename, title, options):
        self.jobs.append((printer, filename, title, options))
        return 7


def test_toner_and_paper_when_driver_is_silent_or_speaks():
    assert toner_text({}) == "неизвестно"
    assert toner_text({"marker-levels": [-1], "marker-high-levels": [100]}) == "неизвестно"
    assert toner_text({"marker-levels": [80, 20], "marker-high-levels": [100, 100]}) == "20%"
    assert toner_text({"marker-levels": [0], "marker-high-levels": [100]}) == "0%"
    assert paper_text({}) == "неизвестно"
    assert paper_text({"printer-state-reasons": ["none"]}) == "да"
    assert paper_text({"printer-state-reasons": ["media-empty-warning"]}) == "нет"
    assert paper_text({"printer-state-reasons": "media-needed"}) == "нет"
    assert paper_text({"printer-state-reasons": ["media-jam"]}) == "да"


def test_status_from_fake_printer(app, client):
    app.state.printer_factory = lambda: FakeCups()
    body = client.get("/api/printer").json()
    assert body["name"] == "Pantum-M6507"
    assert body["toner"] == "50%"
    assert body["paper"] == "да"
    assert body["duplex"] is True
    assert body["state"] == "готов"
    assert body["message"] is None
    assert body["media"][0] == "A4"
    assert body["media_default"] is None


def test_status_when_cups_is_down(app, client):
    def boom():
        raise PrinterError("Нет связи с CUPS")

    app.state.printer_factory = boom
    body = client.get("/api/printer").json()
    assert body["toner"] == "неизвестно"
    assert body["paper"] == "неизвестно"
    assert body["message"] == "Нет связи с CUPS"
    assert body["media"][0] == "A4"
    assert body["media_default"] is None


def test_print_own_pdf_and_reject_bad_settings(app, client):
    fake = FakeCups()
    app.state.printer_factory = lambda: fake
    file_id = upload(client).json()["file"]["id"]
    ok = client.post(
        "/api/print",
        json={"file_id": file_id, "copies": 2, "pages": "1-2, 4", "sides": "long", "media": "A4", "fit": True},
    )
    assert ok.status_code == 200
    assert ok.json()["job_id"] == 7
    printer, filename, _title, options = fake.jobs[0]
    assert printer == "Pantum-M6507"
    assert filename.endswith(".pdf")
    assert options["copies"] == "2"
    assert options["page-ranges"] == "1-2,4"
    assert options["sides"] == "two-sided-long-edge"
    assert options["fit-to-page"] == "true"

    bad_pages = client.post("/api/print", json={"file_id": file_id, "pages": "1-0"})
    assert bad_pages.status_code == 400
    assert len(fake.jobs) == 1


def test_print_png_goes_straight_to_cups(app, client):
    fake = FakeCups(duplex=False)
    app.state.printer_factory = lambda: fake
    file_id = upload(client, "pic.png", b"\x89PNG\r\n\x1a\nfake").json()["file"]["id"]
    denied = client.post("/api/print", json={"file_id": file_id, "sides": "short"})
    assert denied.status_code == 400
    assert fake.jobs == []
    accepted = client.post("/api/print", json={"file_id": file_id, "sides": "none"})
    assert accepted.status_code == 200
    assert fake.jobs[0][1].endswith(".png")


def test_media_puts_a4_first_and_keeps_cups_default():
    body = describe(
        {
            "printer-state": 3,
            "media-supported": ["na_letter_8.5x11in", "iso_a4_210x297mm", "A5", "Custom"],
            "media-default": "na_letter_8.5x11in",
        },
        "M6507",
    )
    assert body["media"] == ["iso_a4_210x297mm", "na_letter_8.5x11in", "A5", "Custom"]
    assert body["media_default"] == "na_letter_8.5x11in"
    assert body["media_labels"]["iso_a4_210x297mm"] == "A4"
    assert body["media_labels"]["na_letter_8.5x11in"] == "Letter"
    assert body["media_labels"]["A5"] == "A5"
    assert body["media_labels"]["Custom"] == "Custom"


def test_media_default_outside_the_list_is_ignored():
    body = describe({"media-supported": ["Letter"], "media-default": "A3"}, "M6507")
    assert body["media"] == ["Letter"]
    assert body["media_default"] is None
    assert body["media_labels"]["Letter"] == "Letter"


def test_print_sends_raw_media_name(app, client):
    fake = FakeCups()
    app.state.printer_factory = lambda: fake

    def attributes(name):
        attrs = FakeCups.getPrinterAttributes(fake, name)
        attrs["media-supported"] = ["na_letter_8.5x11in", "A4"]
        attrs["media-default"] = "A4"
        return attrs

    fake.getPrinterAttributes = attributes
    file_id = upload(client).json()["file"]["id"]
    status = client.get("/api/printer").json()
    assert status["media"][0] == "A4"
    assert status["media_default"] == "A4"
    assert status["media_labels"]["na_letter_8.5x11in"] == "Letter"
    response = client.post("/api/print", json={"file_id": file_id, "media": "na_letter_8.5x11in"})
    assert response.status_code == 200
    assert fake.jobs[0][3]["media"] == "na_letter_8.5x11in"


def test_job_view_reads_ipp_state():
    assert job_view({"job-state": 3}, 7) == {"id": 7, "stage": "queued"}
    assert job_view({"job-state": 4}, 7)["stage"] == "queued"
    assert job_view({"job-state": "5"}, 7)["stage"] == "printing"
    assert job_view({"job-state": 6}, 7)["stage"] == "stopped"
    assert job_view({"job-state": 7}, 7)["stage"] == "canceled"
    assert job_view({"job-state": 8}, 7)["stage"] == "aborted"
    assert job_view({"job-state": 9}, 7)["stage"] == "done"
    assert job_view({}, 7)["stage"] == "unknown"


def test_job_status_follows_cups(app, client):
    fake = FakeCups()
    fake.job_state = 3

    def get_job(job_id):
        assert job_id == 7
        return {"job-state": fake.job_state}

    fake.getJobAttributes = get_job
    app.state.printer_factory = lambda: fake
    file_id = upload(client).json()["file"]["id"]
    printed = client.post("/api/print", json={"file_id": file_id})
    assert printed.status_code == 200
    assert printed.json()["job_id"] == 7
    assert client.get("/api/jobs/7").json() == {"id": 7, "stage": "queued"}
    fake.job_state = 5
    assert client.get("/api/jobs/7").json()["stage"] == "printing"
    fake.job_state = 9
    assert client.get("/api/jobs/7").json()["stage"] == "done"
    assert client.get("/api/jobs/0").status_code == 404


def test_missing_job_and_stranger(app, client):
    fake = FakeCups()

    def missing(job_id):
        raise RuntimeError("нет задания")

    fake.getJobAttributes = missing
    app.state.printer_factory = lambda: fake
    upload(client)
    assert client.get("/api/jobs/4").status_code == 404
    with TestClient(app) as stranger:
        assert stranger.get("/api/jobs/4").status_code == 404


def test_stranger_does_not_ask_cups(app):
    def boom():
        raise AssertionError("cups")

    app.state.printer_factory = boom
    with TestClient(app) as stranger:
        assert stranger.get("/api/jobs/4").status_code == 404


def test_print_office_converts_first(app, client, monkeypatch):
    fake = FakeCups()
    app.state.printer_factory = lambda: fake

    def fake_convert(source, dest):
        dest.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr("server.files.convert_to_pdf", fake_convert)
    file_id = upload(client, "note.docx", b"PK\x03\x04docx").json()["file"]["id"]
    response = client.post("/api/print", json={"file_id": file_id})
    assert response.status_code == 200
    assert fake.jobs[0][1].endswith(".pdf")
    assert "/files/" not in fake.jobs[0][1]
