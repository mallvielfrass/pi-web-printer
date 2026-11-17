"""Сканирование через scanimage (SANE).

Аргументы передаются списком, оболочка не используется.
app.state.scan_to_file можно подменить в тестах.
После успешного скана файл поворачивается, если задан SCAN_ROTATE.
"""

import logging
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from server import db
from server.auth import Db, ensure_user
from server.files import MAX_FILES_PER_USER, file_dict, new_relative

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

RESOLUTIONS = {75, 150, 300, 600}
MODES = {
    "color": "Color",
    "gray": "Gray",
    "lineart": "Lineart",
}
FORMATS = {
    "png": ("png", ".png"),
    "jpeg": ("jpeg", ".jpg"),
    "tiff": ("tiff", ".tiff"),
    "pdf": ("pdf", ".pdf"),
}

# Цветной скан 600 dpi легко занимает десятки мегабайт и память платы.
MAX_SCAN_BYTES = 80 * 1024 * 1024

# По часовой стрелке. 180 — обычный случай этого МФУ: лист выходит вверх ногами.
SCAN_ROTATIONS = {0, 90, 180, 270}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
_FORMAT_BY_EXT = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}


class ScannerError(Exception):
    pass


def parse_scan_rotate(raw: str | None) -> int:
    text = "0" if raw is None or not raw.strip() else raw.strip()
    if text not in {"0", "90", "180", "270"}:
        raise ValueError("SCAN_ROTATE должен быть 0, 90, 180 или 270")
    return int(text)


def rotate_scan(path: Path, degrees: int) -> None:
    """Поворот уже записанного скана. 0 ничего не делает и файл не открывает."""
    if degrees == 0:
        return
    if degrees not in SCAN_ROTATIONS:
        raise ScannerError("Некорректный угол поворота")
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            _rotate_pdf(path, degrees)
        elif ext in _IMAGE_EXT:
            _rotate_image(path, degrees)
        else:
            raise ScannerError("Этот формат скана нельзя повернуть")
    except ScannerError:
        raise
    except Exception as exc:
        log.exception("поворот скана")
        raise ScannerError("Не удалось повернуть скан") from exc


def _rotate_image(path: Path, degrees: int) -> None:
    try:
        from PIL import Image
    except ImportError as exc:
        raise ScannerError("Не установлен Pillow") from exc
    # Pillow крутит против часовой. 90° по часовой — это ROTATE_270.
    clockwise = {
        90: Image.Transpose.ROTATE_270,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_90,
    }
    image = Image.open(path)
    rotated = None
    try:
        fmt = image.format or _FORMAT_BY_EXT[path.suffix.lower()]
        rotated = image.transpose(clockwise[degrees])
    finally:
        image.close()
    tmp = path.with_name(path.name + ".rot")
    try:
        kwargs: dict = {}
        if fmt == "JPEG":
            kwargs["quality"] = 90
            if rotated.mode not in ("L", "RGB"):
                converted = rotated.convert("RGB")
                rotated.close()
                rotated = converted
        elif fmt == "TIFF":
            kwargs["compression"] = "tiff_lzw"
        rotated.save(tmp, format=fmt, **kwargs)
        tmp.chmod(0o600)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        if rotated is not None:
            rotated.close()


def _rotate_pdf(path: Path, degrees: int) -> None:
    # pypdf меняет поле /Rotate. Картинка страницы не распаковывается —
    # на 1 ГБ это легче, чем перерисовать PDF.
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:
        raise ScannerError("Не установлен pypdf") from exc
    reader = PdfReader(str(path))
    writer = None
    tmp = path.with_name(path.name + ".rot")
    try:
        if len(reader.pages) == 0:
            raise ScannerError("В PDF нет страниц")
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
            writer.pages[-1].rotate(degrees)
        with tmp.open("wb") as handle:
            writer.write(handle)
        tmp.chmod(0o600)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        if writer is not None:
            writer.close()
        reader.close()


class ScanIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    resolution: int = 300
    mode: str = "gray"
    format: str = "png"


def scan_to_file(
    dest: Path,
    *,
    resolution: int,
    mode: str,
    image_format: str,
    device: str | None,
) -> None:
    command = [
        "scanimage",
        f"--resolution={resolution}",
        f"--mode={mode}",
        f"--format={image_format}",
        f"--output-file={dest}",
    ]
    if device:
        command.append(f"--device-name={device}")
    try:
        proc = subprocess.run(command, capture_output=True, timeout=180, check=False)
    except FileNotFoundError as exc:
        raise ScannerError("Не найден scanimage. Установите пакет sane-utils") from exc
    except subprocess.TimeoutExpired as exc:
        dest.unlink(missing_ok=True)
        raise ScannerError("Сканер не ответил за 3 минуты") from exc
    if proc.returncode != 0:
        dest.unlink(missing_ok=True)
        lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        tail = lines[-1] if lines else f"код {proc.returncode}"
        raise ScannerError("Не удалось отсканировать: " + tail[:300])


@router.post("/scan")
def scan(body: ScanIn, request: Request, response: Response, conn: Db) -> dict:
    if body.resolution not in RESOLUTIONS:
        raise HTTPException(400, "Некорректное разрешение")
    if body.mode not in MODES:
        raise HTTPException(400, "Некорректный цвет")
    spec = FORMATS.get(body.format)
    if spec is None:
        raise HTTPException(400, "Некорректный формат")
    user = ensure_user(conn, request, response)
    if db.count_files(conn, user["id"]) >= MAX_FILES_PER_USER:
        raise HTTPException(400, "Слишком много файлов. Удалите ненужные")
    image_format, ext = spec
    relative = new_relative(ext)
    path = request.app.state.storage_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        request.app.state.scan_to_file(
            path,
            resolution=body.resolution,
            mode=MODES[body.mode],
            image_format=image_format,
            device=request.app.state.scanner_device,
        )
    except ScannerError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(503, str(exc)) from exc
    except Exception:
        path.unlink(missing_ok=True)
        log.exception("скан не выполнен")
        raise HTTPException(503, "Не удалось отсканировать") from None
    if not path.is_file() or path.stat().st_size == 0:
        path.unlink(missing_ok=True)
        raise HTTPException(503, "Сканер не вернул изображение")
    try:
        rotate_scan(path, request.app.state.scan_rotate)
    except ScannerError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(503, str(exc)) from exc
    size = path.stat().st_size if path.is_file() else 0
    if size == 0:
        path.unlink(missing_ok=True)
        raise HTTPException(503, "Сканер не вернул изображение")
    if size > MAX_SCAN_BYTES:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "Слишком большое изображение, уменьшите разрешение")
    path.chmod(0o600)
    # Часы платы, а не UTC: имя видно в списке рядом с принтером.
    name = f"Скан {datetime.now().strftime('%Y-%m-%d %H-%M')}{ext}"
    try:
        row = db.add_file(conn, user["id"], "scan", name, relative)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"file": file_dict(row)}
