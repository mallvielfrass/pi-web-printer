"""Раз в час удаляет просроченных гостей: файлы на диске, строки и сессии.

Cookie гостя и так живёт не дольше 24 часов. Здесь убирается то,
что осталось на диске, если браузер больше не заходил.
Заодно удаляются просроченные сессии обычных пользователей.
"""

import asyncio
import logging

from fastapi import FastAPI

from server import db
from server.files import absolute_path, remove_previews

log = logging.getLogger(__name__)

INTERVAL_SECONDS = 60 * 60


def delete_expired(conn, storage, preview_dir) -> int:
    now = db.iso(db.utcnow())
    users = db.expired_temporary_users(conn, now)
    for user in users:
        # Сначала диск: после DELETE пути уже не прочитать.
        for row in db.files_of_user(conn, user["id"]):
            path = absolute_path(storage, row["path"])
            if path is not None:
                path.unlink(missing_ok=True)
            remove_previews(preview_dir, row["id"])
        db.delete_user(conn, user["id"])
    db.delete_expired_sessions(conn, now)
    return len(users)


def run_cleanup(app: FastAPI) -> int:
    conn = db.connect(app.state.db_path)
    try:
        removed = delete_expired(conn, app.state.storage_dir, app.state.preview_dir)
    finally:
        conn.close()
    if removed:
        log.info("удалены просроченные гости: %s", removed)
    return removed


async def cleanup_loop(app: FastAPI) -> None:
    while True:
        try:
            await asyncio.to_thread(run_cleanup, app)
        except Exception:
            log.exception("очистка не выполнилась")
        await asyncio.sleep(INTERVAL_SECONDS)
