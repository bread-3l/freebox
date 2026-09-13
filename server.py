"""
Liquid Inbox — простой серверный backend-заглушка.

Запуск:
    python server.py

Структура:
    index.html              — существующий фронтенд
    server.py               — этот сервер
    data/database.json      — серверное хранилище

Google Drive пока НЕ подключён.
Когда будем готовы, функцию GoogleDriveStorage можно заменить на реальную
реализацию через Google Drive API, не меняя API фронтенда.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import json
import os
import secrets
import mimetypes
import time

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_FILE = DATA_DIR / "database.json"
INDEX_FILE = BASE_DIR / "index.html"

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# Временная заглушка администратора.
# Позже заменим на безопасную переменную окружения/нормальную авторизацию.
ADMIN_CODE = os.getenv("ADMIN_CODE", "CHANGE-ME-ADMIN-CODE")

GOOGLE_DRIVE_FOLDER_ID = os.getenv(
    "GOOGLE_DRIVE_FOLDER_ID",
    "14qXbcPrRrRinqdMjptsG_U6GNyWo0N61"
)


def default_db():
    return {
        "version": 1,
        "adminConfig": {
            "code": ADMIN_CODE,
            "ownerId": None
        },
        "backendConfig": {
            "provider": "server",
            "storage": "local-json",
            "googleDrive": {
                "enabled": False,
                "folderId": GOOGLE_DRIVE_FOLDER_ID
            }
        },
        "logs": [],
        "bans": [],
        "visitors": {},
        "fingerprints": {},
        "submissions": [],
        "users": {},
        "settings": {}
    }


def ensure_storage():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not DB_FILE.exists():
        save_db(default_db())


def load_db():
    ensure_storage()

    try:
        with DB_FILE.open("r", encoding="utf-8") as f:
            db = json.load(f)
    except (OSError, json.JSONDecodeError):
        db = default_db()
        save_db(db)

    # Мягкая миграция: добавляем отсутствующие разделы.
    base = default_db()
    changed = False

    for key, value in base.items():
        if key not in db:
            db[key] = value
            changed = True

    if changed:
        save_db(db)

    return db


def save_db(db):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DB_FILE.with_suffix(".tmp")

    with tmp.open("w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    tmp.replace(DB_FILE)


DB = load_db()


def add_log(level, message):
    DB.setdefault("logs", []).insert(0, {
        "ts": int(time.time() * 1000),
        "level": level,
        "message": message
    })

    DB["logs"] = DB["logs"][:400]
    save_db(DB)


# ============================================================
# GOOGLE DRIVE — ЗАГЛУШКА
# ============================================================

class GoogleDriveStorage:
    """
    Здесь позже будет настоящая интеграция с Google Drive.

    Сейчас ничего в Google Drive не отправляется.
    Система специально отделена от остального сервера.
    """

    enabled = False

    def __init__(self, folder_id):
        self.folder_id = folder_id

    def status(self):
        return {
            "enabled": False,
            "connected": False,
            "folderId": self.folder_id,
            "message": "Google Drive пока работает как заглушка"
        }

    def save_json(self, filename, data):
        raise NotImplementedError(
            "Google Drive ещё не подключён. "
            "Сейчас используется data/database.json."
        )

    def upload_file(self, filename, content, content_type):
        raise NotImplementedError(
            "Загрузка в Google Drive ещё не подключена."
        )


DRIVE = GoogleDriveStorage(GOOGLE_DRIVE_FOLDER_ID)


# ============================================================
# HTTP HELPERS
# ============================================================

class Handler(BaseHTTPRequestHandler):

    server_version = "LiquidInboxServer/1.0"

    def log_message(self, fmt, *args):
        # Не засоряем консоль стандартными HTTP-логами.
        print("[HTTP]", fmt % args)

    def send_json(self, data, status=200):
        raw = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":")
        ).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def send_text(self, text, status=200, content_type="text/plain; charset=utf-8"):
        raw = text.encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0

        if length <= 0:
            return {}

        raw = self.rfile.read(length)

        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def admin_token(self):
        auth = self.headers.get("Authorization", "")

        if auth.startswith("Bearer "):
            return auth[7:].strip()

        return ""

    def is_admin(self):
        # Простая временная схема.
        # Для production позже сделаем нормальные сессии.
        token = self.admin_token()
        return bool(token and token == ADMIN_CODE)

    def require_admin(self):
        if not self.is_admin():
            self.send_json({
                "error": "Требуется авторизация администратора"
            }, 401)
            return False

        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/api/health":
                self.send_json({
                    "ok": True,
                    "server": "liquid-inbox",
                    "storage": "local-json",
                    "googleDrive": DRIVE.status(),
                    "time": int(time.time() * 1000)
                })
                return

            if path == "/api/state":
                client_id = query.get("client_id", [""])[0]

                # Возвращаем состояние, совместимое с текущим index.html.
                self.send_json({
                    "db": DB,
                    "isAdmin": self.is_admin(),
                    "clientId": client_id
                })
                return

            if path == "/api/admin/status":
                self.send_json({
                    "isAdmin": self.is_admin(),
                    "googleDrive": DRIVE.status()
                })
                return

            if path == "/api/admin/drive-status":
                if not self.require_admin():
                    return

                self.send_json(DRIVE.status())
                return

            # Раздача index.html.
            if path in ("/", "/index.html"):
                if not INDEX_FILE.exists():
                    self.send_text(
                        "index.html не найден рядом с server.py",
                        404
                    )
                    return

                raw = INDEX_FILE.read_bytes()

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            # Статические файлы проекта.
            file_path = (BASE_DIR / path.lstrip("/")).resolve()

            if BASE_DIR not in file_path.parents and file_path != BASE_DIR:
                self.send_json({"error": "Forbidden"}, 403)
                return

            if file_path.is_file():
                raw = file_path.read_bytes()
                content_type = mimetypes.guess_type(str(file_path))[0]
                content_type = content_type or "application/octet-stream"

                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            self.send_json({"error": "Not found"}, 404)

        except Exception as e:
            print("[ERROR]", repr(e))
            self.send_json({
                "error": "Внутренняя ошибка сервера"
            }, 500)

    def do_POST(self):
        global DB

        parsed = urlparse(self.path)
        path = parsed.path
        body = self.read_json()

        try:
            if path == "/api/admin/login":
                code = str(body.get("code", "")).strip()

                if not code or not secrets.compare_digest(code, ADMIN_CODE):
                    self.send_json({
                        "error": "Неверный код администратора"
                    }, 401)
                    return

                # Пока токеном является тот же секрет.
                # В production заменим на отдельную серверную сессию.
                self.send_json({
                    "ok": True,
                    "token": ADMIN_CODE
                })
                return

            if path == "/api/admin/logout":
                self.send_json({"ok": True})
                return

            if path == "/api/admin/regenerate-code":
                if not self.require_admin():
                    return

                new_code = (
                    secrets.token_hex(4).upper()[:4]
                    + "-"
                    + secrets.token_hex(4).upper()[:4]
                )

                DB["adminConfig"]["code"] = new_code
                save_db(DB)

                self.send_json({"ok": True, "code": new_code})
                return

            if path == "/api/admin/save-state":
                if not self.require_admin():
                    return

                new_db = body.get("db")

                if not isinstance(new_db, dict):
                    self.send_json({
                        "error": "Поле db должно быть объектом"
                    }, 400)
                    return

                DB = new_db
                save_db(DB)

                self.send_json({
                    "ok": True,
                    "db": DB
                })
                return

            if path == "/api/admin/backup":
                if not self.require_admin():
                    return

                # Локальная резервная копия.
                backup_dir = DATA_DIR / "backups"
                backup_dir.mkdir(exist_ok=True)

                filename = (
                    "database-"
                    + time.strftime("%Y%m%d-%H%M%S")
                    + ".json"
                )

                backup_path = backup_dir / filename

                with backup_path.open("w", encoding="utf-8") as f:
                    json.dump(DB, f, ensure_ascii=False, indent=2)

                self.send_json({
                    "ok": True,
                    "filename": filename,
                    "path": str(backup_path.relative_to(BASE_DIR))
                })
                return

            self.send_json({"error": "Not found"}, 404)

        except Exception as e:
            print("[ERROR]", repr(e))
            self.send_json({
                "error": "Внутренняя ошибка сервера"
            }, 500)

    def do_PUT(self):
        # Совместимость с текущим index.html:
        # он использует PUT /api/admin/state.
        if self.path == "/api/admin/state":
            if not self.require_admin():
                return

            body = self.read_json()
            new_db = body.get("db")

            if not isinstance(new_db, dict):
                self.send_json({
                    "error": "Некорректная база данных"
                }, 400)
                return

            global DB
            DB = new_db
            save_db(DB)

            self.send_json({
                "ok": True,
                "db": DB
            })
            return

        self.send_json({"error": "Not found"}, 404)


def main():
    ensure_storage()

    print()
    print("=" * 60)
    print(" Liquid Inbox server")
    print("=" * 60)
    print(f" URL: http://localhost:{PORT}")
    print(f" Data: {DB_FILE}")
    print(f" Google Drive: {DRIVE.status()['message']}")
    print()
    print(" Для остановки нажмите Ctrl+C")
    print("=" * 60)
    print()

    server = ThreadingHTTPServer((HOST, PORT), Handler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
