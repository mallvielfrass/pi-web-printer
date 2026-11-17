"""Сканирование через scanimage (SANE).

Аргументы передаются списком, оболочка не используется.
app.state.scan_to_file можно подменить в тестах.
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


class ScannerError(Exception):
    pass


class ScanIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    resolution: int = 300
    mode: str = "color"
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
