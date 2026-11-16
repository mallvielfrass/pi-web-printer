"""Регистрация, вход и сессия в cookie.

Пароль хранится как scrypt. Параметры подобраны под Pi 3B+ с 1 ГБ:
N=2**14, r=8, p=1 занимают около 16 МБ и долю секунды.
Их значение записано в строке хеша и читается при проверке.
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, field_validator

from server import db

COOKIE = "session"
TEMP_SESSION_SECONDS = 24 * 60 * 60
USER_SESSION_SECONDS = 30 * 24 * 60 * 60

# N выше 2**14 на этой плате заметно тормозит вход и ест память.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1

_NICKNAME = re.compile(r"^[\w.-]{2,32}$", re.UNICODE)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

router = APIRouter(prefix="/api/auth")


class RegisterIn(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    nickname: str | None = None
    email: str | None = None
    password: str

    @field_validator("nickname", "email", mode="before")
    @classmethod
    def blank_is_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    login: str
    password: str


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        salt = bytes.fromhex(parts[4])
        expected = bytes.fromhex(parts[5])
    except ValueError:
        return False
    # Верхняя граница, чтобы подменённая строка в базе не заставила считать гигабайтный scrypt.
    if n < 2 or n > 2**15 or r < 1 or r > 16 or p < 1 or p > 2 or not _power_of_two(n):
        return False
    if not 8 <= len(salt) <= 64 or not expected:
        return False
    try:
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected))
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(actual, expected)


def _power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


# Считается один раз, чтобы ответ «нет такого пользователя» занимал столько же, сколько неверный пароль.
_DUMMY_HASH = hash_password("not-a-real-password")


def password_problem(password: str) -> str | None:
    if len(password) < 8:
        return "Пароль короче 8 символов"
    if len(password) > 128:
        return "Пароль длиннее 128 символов"
    return None


def normalize_nickname(value: str | None) -> str | None:
    if value is None:
        return None
    if not _NICKNAME.fullmatch(value):
        raise HTTPException(400, "Ник: 2–32 символа, буквы, цифры, точка, дефис или _")
    return value


def normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    email = value.lower()
    if len(email) > 254 or not _EMAIL.fullmatch(email):
        raise HTTPException(400, "Некорректная почта")
    return email


def public_user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "nickname": row["nickname"],
        "email": row["email"],
        "temporary": bool(row["is_temporary"]),
    }


def get_db(request: Request):
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


Db = Annotated[sqlite3.Connection, Depends(get_db)]


def user_from_cookie(conn: sqlite3.Connection, request: Request) -> sqlite3.Row | None:
    token = request.cookies.get(COOKIE, "")
    if not 20 <= len(token) <= 200:
        return None
    return db.user_by_token(conn, token)


def set_session_cookie(response: Response, request: Request, token: str, max_age: int) -> None:
    # SameSite=Lax: чужой сайт не отправит cookie вместе с POST.
    # Secure выключен, пока станция открыта по обычному HTTP в локальной сети.
    response.set_cookie(
        COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=bool(request.app.state.cookie_secure),
        path="/",
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=bool(request.app.state.cookie_secure),
    )


def ensure_user(conn: sqlite3.Connection, request: Request, response: Response) -> sqlite3.Row:
    """Текущий пользователь или новый гость на 24 часа.

    Гость появляется в момент первой загрузки или скана, не при открытии страницы.
    """
    current = user_from_cookie(conn, request)
    if current is not None:
        return current
    created = db.create_temporary_user(conn)
    token = db.create_session(conn, created["id"], TEMP_SESSION_SECONDS)
    set_session_cookie(response, request, token, TEMP_SESSION_SECONDS)
    return created


@router.post("/register")
def register(body: RegisterIn, request: Request, response: Response, conn: Db) -> dict:
    current = user_from_cookie(conn, request)
    if current is not None and not current["is_temporary"]:
        raise HTTPException(400, "Вы уже вошли")
    nickname = normalize_nickname(body.nickname)
    email = normalize_email(body.email)
    if nickname is None and email is None:
        raise HTTPException(400, "Нужен ник или почта")
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(400, problem)
    if nickname and db.nickname_taken(conn, nickname):
        raise HTTPException(400, "Такой ник уже занят")
    if email and db.email_taken(conn, email):
        raise HTTPException(400, "Такая почта уже занята")
    try:
        user = db.create_user(conn, nickname, email, hash_password(body.password))
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Такой ник или почта уже заняты") from None
    if current is not None and current["is_temporary"]:
        db.adopt_temporary(conn, current["id"], user["id"])
    token = db.create_session(conn, user["id"], USER_SESSION_SECONDS)
    set_session_cookie(response, request, token, USER_SESSION_SECONDS)
    return {"user": public_user(user)}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, conn: Db) -> dict:
    current = user_from_cookie(conn, request)
    login_text = body.login.strip()
    if not login_text or len(login_text) > 254:
        raise HTTPException(400, "Неверный логин или пароль")
    problem = password_problem(body.password)
    user = db.find_login(conn, login_text) if problem is None else None
    stored = _DUMMY_HASH
    if user is not None and user["password_hash"]:
        stored = user["password_hash"]
    # Длинный пароль всегда прогоняется через scrypt, даже если пользователя нет.
    matches = verify_password(body.password, stored) if problem is None else False
    if user is None or not matches:
        raise HTTPException(400, "Неверный логин или пароль")
    if current is not None and current["is_temporary"] and current["id"] != user["id"]:
        db.adopt_temporary(conn, current["id"], user["id"])
    token = db.create_session(conn, user["id"], USER_SESSION_SECONDS)
    set_session_cookie(response, request, token, USER_SESSION_SECONDS)
    fresh = db.get_user(conn, user["id"])
    return {"user": public_user(fresh if fresh is not None else user)}


@router.post("/logout")
def logout(request: Request, response: Response, conn: Db) -> dict:
    token = request.cookies.get(COOKIE, "")
    if token:
        db.delete_session(conn, token)
    clear_session_cookie(response, request)
    return {"user": None}


@router.get("/me")
def me(request: Request, conn: Db) -> dict:
    user = user_from_cookie(conn, request)
    if user is None:
        return {"user": None}
    return {"user": public_user(user)}
