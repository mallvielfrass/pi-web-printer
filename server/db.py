"""SQLite: пользователи, сессии и файлы.

Соединение открывается там, где оно нужно, и сразу закрывается.
Одно соединение нельзя передавать между потоками.
"""

import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    nickname TEXT UNIQUE,
    email TEXT UNIQUE,
    password_hash TEXT,
    is_temporary INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    CHECK (
        (is_temporary = 1 AND password_hash IS NULL)
        OR (is_temporary = 0 AND password_hash IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('print', 'scan')),
    name TEXT NOT NULL CHECK (length(name) > 0),
    path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_owner_created ON files(owner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_users_temp_expiry ON users(is_temporary, expires_at);
"""

# Имена колонок подставляются в ORDER BY только из этого словаря.
FILE_SORTS = {
    "created_at": "created_at",
    "name": "name",
    "kind": "kind",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    return moment.isoformat()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    path.chmod(0o600)


def create_user(conn: sqlite3.Connection, nickname: str | None, email: str | None, password_hash: str) -> sqlite3.Row:
    now = iso(utcnow())
    try:
        cur = conn.execute(
            """
            INSERT INTO users (nickname, email, password_hash, is_temporary, created_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (nickname, email, password_hash, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise
    user = get_user(conn, cur.lastrowid)
    if user is None:
        raise RuntimeError("пользователь не записался в базу")
    return user


def create_temporary_user(conn: sqlite3.Connection) -> sqlite3.Row:
    now = utcnow()
    cur = conn.execute(
        """
        INSERT INTO users (is_temporary, expires_at, created_at)
        VALUES (1, ?, ?)
        """,
        (iso(now + timedelta(hours=24)), iso(now)),
    )
    conn.commit()
    user = get_user(conn, cur.lastrowid)
    if user is None:
        raise RuntimeError("временный пользователь не записался в базу")
    return user


def get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def nickname_taken(conn: sqlite3.Connection, nickname: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM users WHERE nickname = ? AND is_temporary = 0",
        (nickname,),
    ).fetchone()
    return row is not None


def email_taken(conn: sqlite3.Connection, email: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM users WHERE email = ? AND is_temporary = 0",
        (email,),
    ).fetchone()
    return row is not None


def find_login(conn: sqlite3.Connection, login: str) -> sqlite3.Row | None:
    """Ник сравнивается как введён, почта — в нижнем регистре.

    SQLite NOCASE не меняет регистр кириллицы, поэтому для ника
    отдельная свёртка только мешала бы: «Иван» и «иван» — разные ники.
    """
    login = login.strip()
    if "@" in login:
        return conn.execute(
            "SELECT * FROM users WHERE email = ? AND is_temporary = 0",
            (login.lower(),),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM users WHERE nickname = ? AND is_temporary = 0",
        (login,),
    ).fetchone()


def create_session(conn: sqlite3.Connection, user_id: int, seconds: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = iso(utcnow() + timedelta(seconds=seconds))
    conn.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires),
    )
    conn.commit()
    return token


def user_by_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    now = iso(utcnow())
    row = conn.execute(
        """
        SELECT users.* FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.token = ? AND sessions.expires_at > ?
        """,
        (token, now),
    ).fetchone()
    if row is None:
        return None
    if row["is_temporary"] and row["expires_at"] is not None and row["expires_at"] <= now:
        return None
    return row


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def adopt_temporary(conn: sqlite3.Connection, temp_id: int, user_id: int) -> None:
    """Перевесить файлы гостя на аккаунт. Файлы на диске не двигаются:
    владелец хранится только в колонке owner_id.
    """
    conn.execute(
        "UPDATE files SET owner_id = ? WHERE owner_id = ?",
        (user_id, temp_id),
    )
    conn.execute(
        "DELETE FROM users WHERE id = ? AND is_temporary = 1",
        (temp_id,),
    )
    conn.commit()


def add_file(conn: sqlite3.Connection, owner_id: int, kind: str, name: str, path: str) -> sqlite3.Row:
    cur = conn.execute(
        """
        INSERT INTO files (owner_id, kind, name, path, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (owner_id, kind, name, path, iso(utcnow())),
    )
    conn.commit()
    row = get_file(conn, cur.lastrowid, owner_id)
    if row is None:
        raise RuntimeError("файл не записался в базу")
    return row


def count_files(conn: sqlite3.Connection, owner_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM files WHERE owner_id = ?",
        (owner_id,),
    ).fetchone()
    return int(row["n"])


def list_files(
    conn: sqlite3.Connection,
    owner_id: int,
    *,
    kind: str | None,
    query: str | None,
    sort: str,
    order: str,
    limit: int,
) -> list[sqlite3.Row]:
    column = FILE_SORTS[sort]
    direction = "ASC" if order == "asc" else "DESC"
    where = ["owner_id = ?"]
    args: list[object] = [owner_id]
    if kind is not None:
        where.append("kind = ?")
        args.append(kind)
    if query:
        where.append("name LIKE ? ESCAPE '\\'")
        args.append(_like(query))
    args.append(limit)
    # column и direction выше взяты из фиксированных словарей, не из текста запроса.
    sql = f"""
        SELECT id, name, kind, created_at
        FROM files
        WHERE {" AND ".join(where)}
        ORDER BY {column} {direction}, id {direction}
        LIMIT ?
    """
    return list(conn.execute(sql, args))


def get_file(conn: sqlite3.Connection, file_id: int, owner_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM files WHERE id = ? AND owner_id = ?",
        (file_id, owner_id),
    ).fetchone()


def delete_file_row(conn: sqlite3.Connection, file_id: int, owner_id: int) -> sqlite3.Row | None:
    row = get_file(conn, file_id, owner_id)
    if row is None:
        return None
    conn.execute("DELETE FROM files WHERE id = ? AND owner_id = ?", (file_id, owner_id))
    conn.commit()
    return row


def expired_temporary_users(conn: sqlite3.Connection, now: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT id FROM users
            WHERE is_temporary = 1 AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (now,),
        )
    )


def files_of_user(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT id, path FROM files WHERE owner_id = ?", (user_id,)))


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    # Сессии и строки файлов уходят каскадом. Диск чистит вызывающий код раньше.
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()


def delete_expired_sessions(conn: sqlite3.Connection, now: str) -> None:
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
    conn.commit()


def _like(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
