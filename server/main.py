"""Сборка приложения: маршруты, статика собранного интерфейса, фоновая очистка.

Один процесс. Несколько воркеров uvicorn на Pi 3B+ не запускать:
LibreOffice и scrypt и так занимают память, а замок конвертации — на процесс.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from server import auth, cleanup, db, files, printer, scanner

ROOT = Path(__file__).resolve().parent.parent


def create_app(
    data_dir: Path,
    *,
    max_upload_bytes: int = 50 * 1024 * 1024,
    printer_name: str | None = None,
    scanner_device: str | None = None,
    scan_rotate: int = 0,
    cookie_secure: bool = False,
    dist_dir: Path | None = None,
) -> FastAPI:
    # Документы и база не должны читаться другими локальными пользователями.
    if scan_rotate not in scanner.SCAN_ROTATIONS:
        raise ValueError("SCAN_ROTATE должен быть 0, 90, 180 или 270")
    os.umask(0o077)
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    data_dir.chmod(0o700)
    storage = data_dir / "files"
    previews = data_dir / "previews"
    storage.mkdir(exist_ok=True)
    previews.mkdir(exist_ok=True)
    storage.chmod(0o700)
    previews.chmod(0o700)
    db_path = data_dir / "app.db"
    db.init_db(db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Сразу при старте, иначе после перезагрузки мусор лежит до часа.
        task = asyncio.create_task(cleanup.cleanup_loop(app))
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Принтер", lifespan=lifespan)
    app.state.db_path = db_path
    app.state.storage_dir = storage
    app.state.preview_dir = previews
    app.state.max_upload_bytes = max_upload_bytes
    app.state.printer_name = printer_name
    app.state.scanner_device = scanner_device
    app.state.scan_rotate = scan_rotate
    app.state.cookie_secure = cookie_secure
    app.state.printer_factory = printer.open_cups
    app.state.scan_to_file = scanner.scan_to_file
    app.state.dist_dir = (dist_dir or (ROOT / "web" / "dist")).resolve()

    app.include_router(auth.router)
    app.include_router(files.router)
    app.include_router(printer.router)
    app.include_router(scanner.router)

    @app.middleware("http")
    async def private_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index(request: Request):
        return spa(request.app, "")

    @app.get("/{full_path:path}")
    def frontend(request: Request, full_path: str):
        return spa(request.app, full_path)

    return app


def dist_file(dist: Path, rel: str) -> Path | None:
    if not rel or rel.endswith("/"):
        return None
    root = dist.resolve()
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root) or candidate == root or not candidate.is_file():
        return None
    return candidate


def spa(app: FastAPI, rel: str):
    dist: Path = app.state.dist_dir
    found = dist_file(dist, rel)
    if found is not None:
        return FileResponse(found)
    # Неизвестный адрес API и отсутствующий файл сборки (в имени есть точка)
    # не подменяются страницей: иначе браузер получит HTML вместо скрипта или JSON.
    name = Path(rel).name
    if rel == "api" or rel.startswith("api/") or (name and "." in name):
        raise HTTPException(404)
    index = dist / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(404, "Интерфейс не собран")


def app_from_env() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        megabytes = int(os.environ.get("MAX_UPLOAD_MB", "50"))
    except ValueError:
        raise SystemExit("MAX_UPLOAD_MB должен быть целым числом") from None
    if not 1 <= megabytes <= 200:
        raise SystemExit("MAX_UPLOAD_MB должен быть от 1 до 200")
    try:
        scan_rotate = scanner.parse_scan_rotate(os.environ.get("SCAN_ROTATE"))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    return create_app(
        Path(os.environ.get("DATA_DIR", str(ROOT / "data"))),
        max_upload_bytes=megabytes * 1024 * 1024,
        printer_name=os.environ.get("PRINTER_NAME") or None,
        scanner_device=os.environ.get("SCANNER_DEVICE") or None,
        scan_rotate=scan_rotate,
        cookie_secure=os.environ.get("COOKIE_SECURE") == "1",
    )


app = app_from_env()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
