#!/usr/bin/env python3
"""Movia: a local, dependency-free film library. Python 3.10+."""
import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("MOVIA_DATA_DIR", ROOT / "data")).resolve()
MAX_UPLOAD = 512 * 1024 * 1024
AUTH_ATTEMPTS = defaultdict(deque)
AUTH_LOCK = Lock()


@contextmanager
def connect():
    db = sqlite3.connect(DATA / "movia.sqlite3", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "media").mkdir(exist_ok=True)
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                salt TEXT NOT NULL, password TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                expires REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS movies (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, year INTEGER NOT NULL,
                genre TEXT NOT NULL, duration INTEGER NOT NULL DEFAULT 0,
                description TEXT NOT NULL, image TEXT NOT NULL, video_url TEXT NOT NULL,
                credit TEXT NOT NULL, source TEXT NOT NULL, license TEXT NOT NULL,
                owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS watchlist (
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                movie_id TEXT REFERENCES movies(id) ON DELETE CASCADE,
                created REAL NOT NULL, PRIMARY KEY(user_id, movie_id)
            );
            CREATE TABLE IF NOT EXISTS progress (
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                movie_id TEXT REFERENCES movies(id) ON DELETE CASCADE,
                position REAL NOT NULL, duration REAL NOT NULL, updated REAL NOT NULL,
                PRIMARY KEY(user_id, movie_id)
            );
        """)
        for film in json.loads((ROOT / "catalog.json").read_text()):
            db.execute("""INSERT INTO movies
                (id,title,year,genre,duration,description,image,video_url,credit,source,license)
                VALUES (:id,:title,:year,:genre,:duration,:description,:image,:video_url,:credit,:source,:license)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title, year=excluded.year,
                genre=excluded.genre, duration=excluded.duration, description=excluded.description,
                image=excluded.image, video_url=excluded.video_url, credit=excluded.credit,
                source=excluded.source, license=excluded.license WHERE movies.owner_id IS NULL""", film)
        db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Handler(BaseHTTPRequestHandler):
    server_version = "Movia"

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https: data:; media-src 'self' https: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        super().end_headers()

    def json(self, status, value, cookie=None):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def user(self, db, required=False):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie["movia_session"].value if "movia_session" in cookie else ""
        except Exception:
            token = ""
        user = db.execute("""SELECT users.id, users.username FROM users JOIN sessions
            ON users.id=sessions.user_id WHERE sessions.token=? AND sessions.expires>?""",
            (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if not user and required:
            raise ApiError(401, "Sign in to save your library.")
        return dict(user) if user else None

    def body(self):
        if self.headers.get_content_type() != "application/json":
            raise ApiError(415, "Send JSON data.")
        size = self.length(16_384)
        try:
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError):
            raise ApiError(400, "Invalid JSON data.")

    def length(self, maximum):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "Invalid request size.")
        if size < 0 or size > maximum:
            raise ApiError(413, "File is too large. The upload limit is 512 MB." if maximum == MAX_UPLOAD else "Request too large.")
        return size

    def visible_movie(self, db, movie_id, user):
        movie = db.execute("SELECT * FROM movies WHERE id=? AND (owner_id IS NULL OR owner_id=?)",
                           (movie_id, user["id"] if user else -1)).fetchone()
        if movie is None:
            raise ApiError(404, "Film not found.")
        return dict(movie)

    def safe_url(self, value, optional=False):
        if not isinstance(value, str) or len(value) > 2048:
            raise ApiError(400, "Use a valid HTTPS URL.")
        if optional and not value:
            return ""
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ApiError(400, "Video and image links must start with https://.")
        return value

    def film_data(self, data, user, video_url):
        title = data.get("title", "")
        description = data.get("description", "")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise ApiError(400, "Enter a film title (up to 120 characters).")
        if not isinstance(description, str) or len(description) > 1500:
            raise ApiError(400, "Description must be under 1,500 characters.")
        genre = data.get("genre", "Other")
        if genre not in ["Animation", "Fantasy", "Sci-Fi", "Drama", "Documentary", "Comedy", "Other"]:
            raise ApiError(400, "Choose a valid genre.")
        try:
            year = int(data.get("year", 2026))
        except (ValueError, TypeError):
            raise ApiError(400, "Enter a valid year.")
        if not 1888 <= year <= 2100:
            raise ApiError(400, "Enter a year between 1888 and 2100.")
        if data.get("rights") is not True:
            raise ApiError(400, "Confirm that you have permission to use this video.")
        return dict(id=secrets.token_hex(12), title=title.strip(), year=year, genre=genre,
                    duration=0, description=description.strip(),
                    image=self.safe_url(data.get("image", ""), optional=True) or "/assets/placeholder.svg",
                    video_url=video_url, credit=f"Added by {user['username']}", source="",
                    license="Personal library · Rights confirmed by uploader", owner_id=user["id"])

    def insert_film(self, db, film):
        keys = ",".join(film)
        db.execute(f"INSERT INTO movies ({keys}) VALUES ({','.join('?' for _ in film)})", tuple(film.values()))

    def route(self):
        path = urlsplit(self.path).path
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in allowed:
            raise ApiError(403, "Use the localhost address printed by Movia.")
        if self.command not in ("GET", "HEAD"):
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{host}":
                raise ApiError(403, "Request origin is not allowed.")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise ApiError(403, "Cross-site request is not allowed.")
        with connect() as db:
            user = self.user(db)
            if path == "/api/bootstrap" and self.command == "GET":
                movies = [dict(r) for r in db.execute("SELECT * FROM movies WHERE owner_id IS NULL OR owner_id=? ORDER BY year DESC", (user["id"] if user else -1,))]
                saved, progress = [], {}
                if user:
                    saved = [r[0] for r in db.execute("SELECT movie_id FROM watchlist WHERE user_id=? ORDER BY created DESC", (user["id"],))]
                    progress = {r["movie_id"]: dict(r) for r in db.execute("SELECT * FROM progress WHERE user_id=?", (user["id"],))}
                return self.json(200, dict(user=user, movies=movies, watchlist=saved, progress=progress))
            if path in ("/api/register", "/api/login") and self.command == "POST":
                with AUTH_LOCK:
                    attempts = AUTH_ATTEMPTS[self.client_address[0]]
                    now = time.time()
                    while attempts and attempts[0] < now - 60:
                        attempts.popleft()
                    if len(attempts) >= 12:
                        raise ApiError(429, "Too many attempts. Try again in a minute.")
                    attempts.append(now)
                data = self.body()
                name, password = data.get("username", ""), data.get("password", "")
                if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_]{3,24}", name):
                    raise ApiError(400, "Use 3–24 letters, numbers, or underscores for your username.")
                if not isinstance(password, str) or not 8 <= len(password) <= 128:
                    raise ApiError(400, "Use a password with 8–128 characters.")
                name = name.lower()
                if path == "/api/register":
                    salt = secrets.token_hex(16)
                    try:
                        cur = db.execute("INSERT INTO users(username,salt,password) VALUES(?,?,?)", (name, salt, password_hash(password, salt)))
                    except sqlite3.IntegrityError:
                        raise ApiError(409, "That username is taken.")
                    user = {"id": cur.lastrowid, "username": name}
                else:
                    row = db.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
                    candidate = password_hash(password, row["salt"] if row else "00" * 16)
                    if not row or not hmac.compare_digest(candidate, row["password"]):
                        raise ApiError(401, "Username or password is incorrect.")
                    user = {"id": row["id"], "username": row["username"]}
                token = secrets.token_urlsafe(32)
                db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
                db.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user["id"], time.time()+30*86400))
                db.commit()
                return self.json(200, {"user": user}, f"movia_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000")
            if path == "/api/logout" and self.command == "POST":
                self.body()
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                token = cookie["movia_session"].value if "movia_session" in cookie else ""
                db.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))
                db.commit()
                return self.json(200, {"ok": True}, "movia_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
            match = re.fullmatch(r"/api/(watchlist|progress)/([a-zA-Z0-9_-]+)", path)
            if match and self.command in ("PUT", "DELETE"):
                user = self.user(db, required=True)
                kind, movie_id = match.groups()
                self.visible_movie(db, movie_id, user)
                data = self.body()
                if self.command == "DELETE":
                    db.execute(f"DELETE FROM {kind} WHERE user_id=? AND movie_id=?", (user["id"], movie_id))
                elif kind == "watchlist":
                    db.execute("INSERT OR IGNORE INTO watchlist VALUES(?,?,?)", (user["id"], movie_id, time.time()))
                else:
                    position, duration = data.get("position"), data.get("duration")
                    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 86400 for x in (position, duration)) or position > duration or duration <= 0:
                        raise ApiError(400, "Invalid playback progress.")
                    db.execute("INSERT OR REPLACE INTO progress VALUES(?,?,?,?,?)", (user["id"], movie_id, position, duration, time.time()))
                db.commit()
                return self.json(200, {"ok": True})
            if path == "/api/movies" and self.command == "POST":
                user = self.user(db, required=True)
                data = self.body()
                film = self.film_data(data, user, self.safe_url(data.get("video_url", "")))
                self.insert_film(db, film)
                db.commit()
                return self.json(201, {"movie": film})
            if path == "/api/upload" and self.command == "POST":
                user = self.user(db, required=True)
                size = self.length(MAX_UPLOAD)
                if not size:
                    raise ApiError(400, "Choose a nonempty video file.")
                try:
                    metadata = json.loads(unquote(self.headers.get("X-Movia-Metadata", "")))
                    if not isinstance(metadata, dict):
                        raise ValueError()
                except ValueError:
                    raise ApiError(400, "Missing film details.")
                ext = Path(str(metadata.get("filename", ""))).suffix.lower()
                if ext not in (".mp4", ".webm", ".ogv"):
                    raise ApiError(400, "Choose an MP4, WebM, or OGV file.")
                filename = secrets.token_hex(16) + ext
                film = self.film_data(metadata, user, f"/media/{filename}")
                target = DATA / "media" / filename
                try:
                    remaining = size
                    with target.open("xb") as output:
                        while remaining:
                            chunk = self.rfile.read(min(1024*1024, remaining))
                            if not chunk:
                                raise ApiError(400, "Upload interrupted. Please try again.")
                            output.write(chunk)
                            remaining -= len(chunk)
                    self.insert_film(db, film)
                    db.commit()
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
                return self.json(201, {"movie": film})
            match = re.fullmatch(r"/api/movies/([a-zA-Z0-9_-]+)", path)
            if match and self.command == "DELETE":
                user = self.user(db, required=True)
                self.body()
                film = self.visible_movie(db, match[1], user)
                if film["owner_id"] != user["id"]:
                    raise ApiError(403, "Only your own films can be deleted.")
                db.execute("DELETE FROM movies WHERE id=?", (film["id"],))
                db.commit()
                if film["video_url"].startswith("/media/"):
                    (DATA / "media" / Path(film["video_url"]).name).unlink(missing_ok=True)
                return self.json(200, {"ok": True})
            if path.startswith("/api/"):
                raise ApiError(404, "Endpoint not found.")
            if self.command not in ("GET", "HEAD"):
                raise ApiError(405, "Method not allowed.")
            if path.startswith("/media/"):
                user = self.user(db, required=True)
                if not re.fullmatch(r"/media/[0-9a-f]{32}\.(mp4|webm|ogv)", path):
                    raise ApiError(404, "Video not found.")
                row = db.execute("SELECT id FROM movies WHERE video_url=? AND owner_id=?", (path, user["id"])).fetchone()
                if not row:
                    raise ApiError(404, "Video not found.")
                return self.file(DATA / "media" / Path(path).name, private=True)
            static = ROOT / "static"
            filename = (static / unquote(path).lstrip("/")).resolve() if path != "/" else static / "index.html"
            if not filename.is_relative_to(static) or not filename.is_file():
                raise ApiError(404, "File not found.")
            return self.file(filename)

    def file(self, filename, private=False):
        if not filename.is_file():
            raise ApiError(404, "File not found.")
        size = filename.stat().st_size
        start, end, status = 0, size - 1, 200
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or not any(match.groups()) or size == 0:
                return self.range_error(size)
            a, b = match.groups()
            if a:
                start = int(a)
                end = min(int(b), size - 1) if b else size - 1
            else:
                if int(b) == 0:
                    return self.range_error(size)
                start = max(0, size - int(b))
            if start >= size or start > end:
                return self.range_error(size)
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(filename)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(max(0, end-start+1)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, no-store" if private else "no-cache")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with filename.open("rb") as source:
            source.seek(start)
            remaining = end-start+1
            while remaining > 0:
                chunk = source.read(min(256*1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def range_error(self, size):
        self.send_response(416)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def handle_request(self):
        self.connection.settimeout(120)
        try:
            self.route()
        except ApiError as error:
            self.close_connection = True
            self.json(error.status, {"error": error.message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception as error:
            self.close_connection = True
            self.log_error("Request failed: %s", type(error).__name__)
            self.json(500, {"error": "Something went wrong. Please try again."})

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = handle_request


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    initialize()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"\n  MOVIA  →  http://localhost:{args.port}\n  Database: {DATA / 'movia.sqlite3'}\n  Press Ctrl+C to stop.\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMovia stopped.")
    finally:
        server.server_close()
