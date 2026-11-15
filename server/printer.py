"""Состояние принтера и печать через CUPS.

Тонер — это marker-levels и marker-high-levels.
Отдельного признака «бумага есть» у CUPS нет, он выводится из
printer-state-reasons. Если драйвер эти поля не прислал, в ответе
«неизвестно».

app.state.printer_factory возвращает соединение. В тестах туда
кладётся подмена, pycups и cupsd не нужны.
"""

import logging
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from server import db
from server.auth import Db, user_from_cookie
from server.files import DIRECT_PRINT, ConversionError, absolute_path, pdf_for_preview

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

DEFAULT_MEDIA = ["A4", "A5", "Letter"]
_EMPTY_PAPER = {"media-empty", "media-needed"}
_STATES = {3: "готов", 4: "печатает", 5: "остановлен"}
_MEDIA_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
_PAGES = re.compile(r"^\d{1,4}(-\d{1,4})?(,\d{1,4}(-\d{1,4})?)*$")
_SIDES = {
    "none": "one-sided",
    "long": "two-sided-long-edge",
    "short": "two-sided-short-edge",
}


class PrinterError(Exception):
    pass


class PrintIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    file_id: int
    copies: int = 1
    pages: str = ""
    sides: str = "none"
    media: str = "A4"
    fit: bool = True


def open_cups():
    try:
        import cups
    except ImportError as exc:
        raise PrinterError("Не установлен модуль cups (пакет python3-cups)") from exc
    try:
        return cups.Connection()
    except Exception as exc:
        raise PrinterError("Нет связи с CUPS") from exc


def describe(attrs: dict, name: str) -> dict:
    return {
        "name": name,
        "toner": toner_text(attrs),
        "paper": paper_text(attrs),
        "duplex": duplex_supported(attrs),
        "media": media_list(attrs),
        "state": state_text(attrs),
        "message": None,
    }


def unknown_status(name: str, message: str) -> dict:
    return {
        "name": name,
        "toner": "неизвестно",
        "paper": "неизвестно",
        "duplex": False,
        "media": list(DEFAULT_MEDIA),
        "state": "неизвестно",
        "message": message,
    }


def toner_text(attrs: dict) -> str:
    levels = _ints(attrs.get("marker-levels"))
    highs = _ints(attrs.get("marker-high-levels"))
    if not levels or not highs:
        return "неизвестно"
    percents = []
    for level, high in zip(levels, highs):
        # Отрицательные значения IPP — «неизвестно», а не процент.
        if level < 0 or high <= 0:
            continue
        percents.append(max(0, min(100, round(level * 100 / high))))
    if not percents:
        return "неизвестно"
    # Несколько картриджей: показываем тот, что закончится раньше.
    # У M6507 он один.
    return f"{min(percents)}%"


def paper_text(attrs: dict) -> str:
    if "printer-state-reasons" not in attrs:
        return "неизвестно"
    reasons = _strings(attrs.get("printer-state-reasons"))
    if not reasons:
        return "неизвестно"
    bases = {_reason_base(item) for item in reasons}
    if bases & _EMPTY_PAPER:
        return "нет"
    return "да"


def duplex_supported(attrs: dict) -> bool:
    return any(item.startswith("two-sided") for item in _strings(attrs.get("sides-supported")))


def media_list(attrs: dict) -> list[str]:
    found: list[str] = []
    for item in _strings(attrs.get("media-supported")):
        if item not in found and _MEDIA_NAME.fullmatch(item):
            found.append(item)
        if len(found) == 30:
            break
    return found or list(DEFAULT_MEDIA)


def state_text(attrs: dict) -> str:
    try:
        return _STATES.get(int(attrs.get("printer-state")), "неизвестно")
    except (TypeError, ValueError):
        return "неизвестно"


def read_status(connection, preferred: str | None) -> dict:
    try:
        printers = connection.getPrinters()
    except Exception as exc:
        raise PrinterError("Не удалось получить список принтеров") from exc
    if not isinstance(printers, dict):
        raise PrinterError("CUPS вернул неожиданный ответ")
    if preferred:
        if preferred not in printers:
            return unknown_status(preferred, "Принтер не найден в CUPS")
        name = preferred
    elif not printers:
        return unknown_status("неизвестно", "В CUPS нет принтеров")
    else:
        name = next(iter(printers))
    try:
        attrs = connection.getPrinterAttributes(name)
    except Exception as exc:
        raise PrinterError("Не удалось прочитать состояние принтера") from exc
    if not isinstance(attrs, dict):
        raise PrinterError("CUPS вернул неожиданный ответ")
    return describe(attrs, name)


def pages_ok(pages: str) -> bool:
    if not _PAGES.fullmatch(pages):
        return False
    for part in pages.split(","):
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start < 1 or end < start:
                return False
        elif int(part) < 1:
            return False
    return True


def cups_options(copies: int, pages: str, sides: str, media: str, fit: bool) -> dict[str, str]:
    options = {
        "copies": str(copies),
        "media": media,
        "sides": _SIDES[sides],
    }
    if pages:
        options["page-ranges"] = pages
    if fit:
        options["fit-to-page"] = "true"
    return options


def _job_title(name: str) -> str:
    clean = "".join(ch for ch in name if ch.isprintable()).strip()
    return (clean or "document")[:80]


def _strings(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (bytes, str)):
        value = [value]
    result = []
    try:
        items = list(value)  # type: ignore[arg-type]
    except TypeError:
        return []
    for item in items:
        if isinstance(item, bytes):
            item = item.decode("utf-8", "replace")
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _ints(value: object) -> list[int]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, (bytes, str)):
        return []
    result = []
    try:
        items = list(value)  # type: ignore[arg-type]
    except TypeError:
        return []
    for item in items:
        if isinstance(item, bool):
            return []
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            return []
    return result


def _reason_base(reason: str) -> str:
    for suffix in ("-error", "-warning", "-report"):
        if reason.endswith(suffix):
            return reason[: -len(suffix)]
    return reason


@router.get("/printer")
def printer_status(request: Request) -> dict:
    try:
        connection = request.app.state.printer_factory()
        return read_status(connection, request.app.state.printer_name)
    except PrinterError as exc:
        return unknown_status("неизвестно", str(exc))


@router.post("/print")
def print_document(body: PrintIn, request: Request, conn: Db) -> dict:
    user = user_from_cookie(conn, request)
    if user is None:
        raise HTTPException(404, "Файл не найден")
    row = db.get_file(conn, body.file_id, user["id"])
    if row is None:
        raise HTTPException(404, "Файл не найден")
    if body.copies < 1 or body.copies > 99:
        raise HTTPException(400, "Количество копий: от 1 до 99")
    if body.sides not in _SIDES:
        raise HTTPException(400, "Некорректный режим сторон")
    pages = "".join((body.pages or "").split())
    if pages and not pages_ok(pages):
        raise HTTPException(400, "Некорректный диапазон страниц")
    path = absolute_path(request.app.state.storage_dir, row["path"])
    if path is None or not path.is_file():
        raise HTTPException(404, "Файл не найден")
    try:
        connection = request.app.state.printer_factory()
        status = read_status(connection, request.app.state.printer_name)
    except PrinterError as exc:
        raise HTTPException(503, str(exc)) from exc
    if status["message"]:
        raise HTTPException(503, status["message"])
    if body.sides != "none" and not status["duplex"]:
        raise HTTPException(400, "Принтер не умеет двустороннюю печать")
    if body.media not in status["media"]:
        raise HTTPException(400, "Такой формат бумаги недоступен")
    printable = path
    if path.suffix.lower() not in DIRECT_PRINT:
        dest = request.app.state.preview_dir / f"{row['id']}.pdf"
        try:
            printable = pdf_for_preview(path, dest)
        except ConversionError as exc:
            raise HTTPException(503, str(exc)) from exc
    options = cups_options(body.copies, pages, body.sides, body.media, body.fit)
    try:
        job_id = connection.printFile(status["name"], str(printable), _job_title(row["name"]), options)
    except Exception:
        log.exception("печать не принята, file_id=%s", row["id"])
        raise HTTPException(503, "CUPS не принял задание") from None
    return {"job_id": job_id}
