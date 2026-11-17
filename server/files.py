"""Файлы пользователя: загрузка, список, скачивание, удаление, предпросмотр.

На диске лежат под случайными именами в одном каталоге.
Кто владелец — только колонка owner_id. Поэтому при регистрации
перенос файлов гостя не двигает сами файлы.
"""

import logging
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

from server import db
from server.auth import Db, ensure_user, user_from_cookie

log = logging.getLogger(__name__)

# Гость создаётся без пароля с любого устройства в сети.
# Предел не даёт забить карту памяти пачкой загрузок.
MAX_FILES_PER_USER = 100

# Прямо в CUPS, без LibreOffice. Картинки для экрана уменьшаются отдельно:
# скан A4 целиком по Wi-Fi телефон открывает очень долго.
DIRECT_PRINT = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".txt"}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
_PREVIEW_AS_IS = {
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
}

# Длинная сторона около 1600 px: на телефоне текст ещё читается, файл уже не мегабайты.
PREVIEW_LONG_SIDE = 1600
PREVIEW_JPEG_QUALITY = 80

router = APIRouter(prefix="/api/files")

# soffice держит профиль и плохо переносит два одновременных запуска.
# На плате с 1 ГБ второй процесс ещё и некуда класть.
_convert_lock = threading.Lock()
# Два больших скана сразу декодировать некуда: на 1 ГБ это легко съедает память.
_preview_lock = threading.Lock()


class ConversionError(Exception):
    pass


class PreviewError(Exception):
    pass


def _is_pdf(data: bytes) -> bool:
    return data.startswith(b"%PDF-")


def _is_png(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n")


def _is_jpeg(data: bytes) -> bool:
    return data.startswith(b"\xff\xd8\xff")


def _is_tiff(data: bytes) -> bool:
    return data.startswith((b"II*\x00", b"MM\x00*"))


def _is_ole(data: bytes) -> bool:
    return data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")


def _is_zip(data: bytes) -> bool:
    return data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))


def _is_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


# Проверяется сигнатура, не весь формат: отсекает переименованный файл,
# но не обещает, что документ откроется.
# SVG нет: браузер выполнил бы скрипт с нашего адреса.
ALLOWED: dict[str, Callable[[bytes], bool]] = {
    ".pdf": _is_pdf,
    ".jpg": _is_jpeg,
    ".jpeg": _is_jpeg,
    ".png": _is_png,
    ".tif": _is_tiff,
    ".tiff": _is_tiff,
    ".txt": _is_text,
    ".doc": _is_ole,
    ".xls": _is_ole,
    ".ppt": _is_ole,
    ".docx": _is_zip,
    ".xlsx": _is_zip,
    ".pptx": _is_zip,
    ".odt": _is_zip,
}

_DOWNLOAD_TYPE = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".txt": "text/plain; charset=utf-8",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def file_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "created_at": row["created_at"],
    }


def display_name(original: str | None, ext: str) -> str:
    raw = Path(original or "").name
    cleaned = "".join(ch for ch in raw if ch.isprintable() and ch not in "/\\")
    cleaned = cleaned.strip().strip(".")
    if not cleaned:
        cleaned = "файл" + ext
    return cleaned[:180]


def new_relative(ext: str) -> str:
    return f"{uuid.uuid4().hex}{ext}"


def absolute_path(storage: Path, relative: str) -> Path | None:
    """Путь внутри каталога хранилища. None, если в базе мусор или `..`."""
    if not relative or relative.startswith(".") or Path(relative).name != relative:
        return None
    root = storage.resolve()
    path = (root / relative).resolve()
    if path.parent != root:
        return None
    return path


def write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o600)


def read_upload(upload: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, "Файл больше допустимого размера")
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(400, "Пустой файл")
    return b"".join(chunks)


def pdf_for_preview(source: Path, dest: Path) -> Path:
    with _convert_lock:
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        convert_to_pdf(source, dest)
        return dest


def remove_previews(preview_dir: Path, file_id: int) -> None:
    """PDF от LibreOffice и JPEG картинки лежат рядом и удаляются вместе с файлом."""
    for suffix in (".pdf", ".jpg"):
        (preview_dir / f"{file_id}{suffix}").unlink(missing_ok=True)


def jpeg_for_preview(source: Path, dest: Path) -> Path:
    """Уменьшенный JPEG в каталоге превью. Повторный запрос берёт уже готовый файл."""
    with _preview_lock:
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Рядом с итогом, чтобы replace не переходил на другой диск.
        tmp = dest.with_name(dest.name + ".part")
        try:
            _write_jpeg_preview(source, tmp)
            tmp.chmod(0o600)
            tmp.replace(dest)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return dest


def _write_jpeg_preview(source: Path, dest: Path) -> None:
    try:
        from PIL import Image, ImageOps
        from PIL.ExifTags import Base
    except ImportError as exc:
        raise PreviewError("Не установлен Pillow") from exc
    image = None
    try:
        image = Image.open(source)
        # draft просит JPEG и часть TIFF отдать уже уменьшенную копию,
        # до полной распаковки. У PNG такого режима нет.
        side = (PREVIEW_LONG_SIDE, PREVIEW_LONG_SIDE)
        if image.mode in ("L", "1"):
            image.draft("L", side)
        else:
            image.draft("RGB", side)
        # thumbnail уменьшает на месте, вторую полноразмерную копию не делаем.
        image.thumbnail(side, Image.Resampling.BICUBIC)
        orientation = image.getexif().get(Base.Orientation)
        if orientation not in (None, 1):
            turned = ImageOps.exif_transpose(image)
            image.close()
            image = turned
        ready = _for_jpeg(image)
        if ready is not image:
            image.close()
            image = ready
        image.save(dest, "JPEG", quality=PREVIEW_JPEG_QUALITY)
    except PreviewError:
        raise
    except Exception as exc:
        log.warning("предпросмотр %s: %s", source.name, exc)
        raise PreviewError("Не удалось подготовить предпросмотр") from exc
    finally:
        if image is not None:
            image.close()


def _for_jpeg(image):
    """JPEG не хранит палитру и прозрачность. Уже RGB или серый возвращаем как есть."""
    from PIL import Image

    if image.mode in ("RGB", "L"):
        return image
    if image.mode in ("RGBA", "LA"):
        flat = Image.new("RGB", image.size, (255, 255, 255))
        flat.paste(image, mask=image.getchannel("A"))
        return flat
    if image.mode == "1":
        return image.convert("L")
    return image.convert("RGB")


def convert_to_pdf(source: Path, dest: Path) -> None:
    binary = shutil.which("libreoffice") or shutil.which("soffice")
    if binary is None:
        raise ConversionError("Не установлен LibreOffice, файл нельзя показать или напечатать")
    with tempfile.TemporaryDirectory(prefix="lo-") as tmp:
        profile = Path(tmp) / "profile"
        out = Path(tmp) / "out"
        profile.mkdir()
        out.mkdir()
        # Свой каталог профиля: общий профиль LibreOffice блокирует второй запуск.
        command = [
            binary,
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out),
            f"-env:UserInstallation={profile.as_uri()}",
            str(source),
        ]
        try:
            proc = subprocess.run(command, capture_output=True, timeout=180, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ConversionError("LibreOffice не успел преобразовать файл") from exc
        produced = next(out.glob("*.pdf"), None)
        if proc.returncode != 0 or produced is None:
            tail = proc.stderr[-300:] if proc.stderr else b""
            log.warning("libreoffice: %s", tail.decode("utf-8", "replace"))
            raise ConversionError("Не удалось преобразовать файл в PDF")
        shutil.move(str(produced), dest)
        dest.chmod(0o600)


def _owned(conn: sqlite3.Connection, request: Request, file_id: int) -> tuple[sqlite3.Row, Path]:
    # Чужой и несуществующий файл отвечают одинаково, без уточнения.
    user = user_from_cookie(conn, request)
    if user is None:
        raise HTTPException(404, "Файл не найден")
    row = db.get_file(conn, file_id, user["id"])
    if row is None:
        raise HTTPException(404, "Файл не найден")
    path = absolute_path(request.app.state.storage_dir, row["path"])
    if path is None or not path.is_file():
        raise HTTPException(404, "Файл не найден")
    return row, path


@router.get("")
def listing(
    request: Request,
    conn: Db,
    kind: str | None = None,
    q: str | None = None,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = 50,
) -> dict:
    if kind is not None and kind not in {"print", "scan"}:
        raise HTTPException(400, "Некорректный тип")
    if sort not in db.FILE_SORTS:
        raise HTTPException(400, "Некорректная сортировка")
    if order not in {"asc", "desc"}:
        raise HTTPException(400, "Некорректный порядок")
    user = user_from_cookie(conn, request)
    if user is None:
        return {"files": []}
    if q is not None:
        q = q.strip()
        if len(q) > 100:
            raise HTTPException(400, "Слишком длинный поиск")
        if not q:
            q = None
    bounded = min(max(limit, 1), 200)
    rows = db.list_files(
        conn,
        user["id"],
        kind=kind,
        query=q,
        sort=sort,
        order=order,
        limit=bounded,
    )
    return {"files": [file_dict(row) for row in rows]}


@router.post("")
def upload(
    request: Request,
    response: Response,
    conn: Db,
    file: UploadFile = File(...),
) -> dict:
    # Синхронный маршрут: соединение SQLite открывается в том же потоке.
    # async-функция получила бы его из пула потоков и упала бы на проверке потока.
    ext = Path(file.filename or "").suffix.lower()
    checker = ALLOWED.get(ext)
    if checker is None:
        raise HTTPException(400, "Такой тип файла печатать нельзя")
    data = read_upload(file, request.app.state.max_upload_bytes)
    if not checker(data):
        raise HTTPException(400, "Содержимое файла не совпадает с расширением")
    user = ensure_user(conn, request, response)
    if db.count_files(conn, user["id"]) >= MAX_FILES_PER_USER:
        raise HTTPException(400, "Слишком много файлов. Удалите ненужные")
    relative = new_relative(ext)
    path = request.app.state.storage_dir / relative
    write_private(path, data)
    try:
        row = db.add_file(conn, user["id"], "print", display_name(file.filename, ext), relative)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"file": file_dict(row)}


@router.get("/{file_id}")
def one(file_id: int, request: Request, conn: Db) -> dict:
    row, _path = _owned(conn, request, file_id)
    return {"file": file_dict(row)}


@router.get("/{file_id}/download")
def download(file_id: int, request: Request, conn: Db) -> FileResponse:
    row, path = _owned(conn, request, file_id)
    media = _DOWNLOAD_TYPE.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, filename=row["name"])


@router.get("/{file_id}/preview")
def preview(file_id: int, request: Request, conn: Db) -> FileResponse:
    row, path = _owned(conn, request, file_id)
    ext = path.suffix.lower()
    if ext in _IMAGE_EXT:
        dest = request.app.state.preview_dir / f"{row['id']}.jpg"
        try:
            jpeg = jpeg_for_preview(path, dest)
        except PreviewError as exc:
            raise HTTPException(503, str(exc)) from exc
        preview_name = f"{Path(row['name']).stem}.jpg"
        return FileResponse(
            jpeg,
            media_type="image/jpeg",
            filename=preview_name,
            content_disposition_type="inline",
        )
    media = _PREVIEW_AS_IS.get(ext)
    if media is not None:
        return FileResponse(path, media_type=media, filename=row["name"], content_disposition_type="inline")
    dest = request.app.state.preview_dir / f"{row['id']}.pdf"
    try:
        pdf = pdf_for_preview(path, dest)
    except ConversionError as exc:
        raise HTTPException(503, str(exc)) from exc
    preview_name = f"{Path(row['name']).stem}.pdf"
    return FileResponse(pdf, media_type="application/pdf", filename=preview_name, content_disposition_type="inline")


@router.delete("/{file_id}")
def remove(file_id: int, request: Request, conn: Db) -> dict:
    user = user_from_cookie(conn, request)
    if user is None:
        raise HTTPException(404, "Файл не найден")
    row = db.delete_file_row(conn, file_id, user["id"])
    if row is None:
        raise HTTPException(404, "Файл не найден")
    path = absolute_path(request.app.state.storage_dir, row["path"])
    if path is not None:
        path.unlink(missing_ok=True)
    remove_previews(request.app.state.preview_dir, file_id)
    return {"ok": True}
