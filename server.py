"""
Freebox server + Google Drive storage.

Local test:
    python server.py

Render:
    Build: pip install -r requirements.txt
    Start: python server.py

Google Drive:
    GOOGLE_SERVICE_ACCOUNT_FILE=/etc/secrets/google-service-account.json
    GOOGLE_DRIVE_FOLDER_ID=<your folder id>

The server falls back to data/database.json when Google Drive is not configured.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import base64
import io
import json
import mimetypes
import os
import secrets
import time

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_FILE = DATA_DIR / "database.json"

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "10000"))
ADMIN_CODE = os.getenv("ADMIN_CODE", "CHANGE-ME-ADMIN-CODE")
DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "14qXbcPrRrRinqdMjptsG_U6GNyWo0N61")
DRIVE_CREDENTIALS_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "/etc/secrets/google-service-account.json"
)
DB_FILENAME = "freebox_database.json"
UPLOADS_FOLDER_NAME = "Freebox uploads"
# Only set this if frontend and backend are ever deployed on DIFFERENT origins
# (e.g. frontend on GitHub Pages, backend on Render). If index.html is served
# by this same server (the default / recommended setup), leave this unset —
# same-origin requests don't need CORS headers at all.
CORS_ORIGIN = os.getenv("CORS_ORIGIN", "").strip()

DRIVE_MIME_FOLDER = "application/vnd.google-apps.folder"

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
    GOOGLE_LIBS_OK = True
except Exception as e:
    GOOGLE_LIBS_OK = False
    GOOGLE_IMPORT_ERROR = str(e)


def default_db():
    return {
        "version": 2,
        "adminConfig": {"code": ADMIN_CODE, "ownerId": None},
        "backendConfig": {
            "provider": "server",
            "storage": "google-drive" if GOOGLE_LIBS_OK else "local-json",
            "googleDrive": {
                "enabled": False,
                "folderId": DRIVE_FOLDER_ID
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


def ensure_local():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_local():
    ensure_local()
    if not DB_FILE.exists():
        db = default_db()
        save_local(db)
        return db
    try:
        return json.loads(DB_FILE.read_text(encoding="utf-8"))
    except Exception:
        db = default_db()
        save_local(db)
        return db


def save_local(db):
    ensure_local()
    tmp = DB_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DB_FILE)


class DriveStorage:
    def __init__(self):
        self.service = None
        self.root_id = DRIVE_FOLDER_ID
        self.db_file_id = None
        self.uploads_folder_id = None
        self.error = ""
        self._connect()

    @property
    def enabled(self):
        return self.service is not None

    def _connect(self):
        if not GOOGLE_LIBS_OK:
            self.error = f"Google libraries unavailable: {GOOGLE_IMPORT_ERROR}"
            return
        if not Path(DRIVE_CREDENTIALS_FILE).exists():
            self.error = f"Service account file not found: {DRIVE_CREDENTIALS_FILE}"
            return
        try:
            scopes = ["https://www.googleapis.com/auth/drive"]
            creds = service_account.Credentials.from_service_account_file(
                DRIVE_CREDENTIALS_FILE,
                scopes=scopes
            )
            self.service = build("drive", "v3", credentials=creds, cache_discovery=False)
            self._ensure_root_access()
            self.db_file_id = self._find_file(DB_FILENAME, self.root_id)
            self.uploads_folder_id = self._find_file(UPLOADS_FOLDER_NAME, self.root_id)
            if self.uploads_folder_id is None:
                self.uploads_folder_id = self._create_folder(
                    UPLOADS_FOLDER_NAME, self.root_id
                )
        except Exception as e:
            self.service = None
            self.error = str(e)

    def _ensure_root_access(self):
        self.service.files().get(
            fileId=self.root_id,
            fields="id,name,mimeType"
        ).execute()

    def _find_file(self, name, parent_id):
        q = (
            f"name = '{name.replace(chr(39), chr(92)+chr(39))}' "
            f"and '{parent_id}' in parents and trashed = false"
        )
        result = self.service.files().list(
            q=q,
            spaces="drive",
            fields="files(id,name,mimeType)",
            pageSize=10
        ).execute()
        files = result.get("files", [])
        return files[0]["id"] if files else None

    def _create_folder(self, name, parent_id):
        body = {
            "name": name,
            "mimeType": DRIVE_MIME_FOLDER,
            "parents": [parent_id]
        }
        result = self.service.files().create(body=body, fields="id").execute()
        return result["id"]

    def read_db(self):
        if not self.enabled:
            return None
        if not self.db_file_id:
            return default_db()
        raw = self.service.files().get(
            fileId=self.db_file_id,
            alt="media"
        ).execute()
        if isinstance(raw, bytes):
            return json.loads(raw.decode("utf-8"))
        if hasattr(raw, "decode"):
            return json.loads(raw.decode("utf-8"))
        return json.loads(raw)

    def write_db(self, db):
        if not self.enabled:
            return False
        payload = json.dumps(db, ensure_ascii=False, indent=2).encode("utf-8")
        media = MediaIoBaseUpload(
            io.BytesIO(payload),
            mimetype="application/json",
            resumable=False
        )
        if self.db_file_id:
            self.service.files().update(
                fileId=self.db_file_id,
                media_body=media
            ).execute()
        else:
            body = {
                "name": DB_FILENAME,
                "parents": [self.root_id],
                "mimeType": "application/json"
            }
            result = self.service.files().create(
                body=body,
                media_body=media,
                fields="id"
            ).execute()
            self.db_file_id = result["id"]
        return True

    def upload_bytes(self, name, content_type, data):
        if not self.enabled:
            raise RuntimeError(self.error or "Google Drive is not configured")
        unique_name = f"{int(time.time())}_{secrets.token_hex(4)}_{name}"
        media = MediaIoBaseUpload(
            io.BytesIO(data),
            mimetype=content_type or "application/octet-stream",
            resumable=False
        )
        body = {
            "name": unique_name,
            "parents": [self.uploads_folder_id]
        }
        result = self.service.files().create(
            body=body,
            media_body=media,
            fields="id,name,size,mimeType,webViewLink"
        ).execute()
        return result

    def status(self):
        return {
            "enabled": self.enabled,
            "connected": self.enabled,
            "folderId": self.root_id,
            "uploadsFolderId": self.uploads_folder_id,
            "error": self.error or None
        }

    def live_test(self):
        """Full round-trip: auth -> folder access -> create test file ->
        confirm file id -> delete it. Does NOT touch DB/uploads content.
        Used by GET /api/admin/drive-test so you can verify real write
        access without going through the frontend at all."""
        if not self.enabled:
            return {"ok": False, "stage": "connect", "error": self.error or "Drive not connected"}
        try:
            name = f"freebox_drive_test_{int(time.time())}.txt"
            media = MediaIoBaseUpload(
                io.BytesIO(b"freebox drive connectivity test"),
                mimetype="text/plain",
                resumable=False
            )
            created = self.service.files().create(
                body={"name": name, "parents": [self.root_id]},
                media_body=media,
                fields="id,name,webViewLink"
            ).execute()
        except Exception as e:
            return {"ok": False, "stage": "create_test_file", "error": str(e)}
        try:
            self.service.files().delete(fileId=created["id"]).execute()
        except Exception as e:
            # File WAS created (so write access is proven) even if cleanup failed.
            return {"ok": True, "stage": "cleanup_failed",
                    "fileId": created["id"], "warning": str(e)}
        return {"ok": True, "stage": "complete", "fileIdWasDeleted": created["id"]}


DRIVE = DriveStorage()

if DRIVE.enabled:
    try:
        DB = DRIVE.read_db() or default_db()
        save_local(DB)
    except Exception as e:
        print("[DRIVE] Could not read DB:", e)
        DB = load_local()
else:
    DB = load_local()

DB.setdefault("adminConfig", {}).setdefault("code", ADMIN_CODE)


def save_db():
    save_local(DB)
    if DRIVE.enabled:
        try:
            DRIVE.write_db(DB)
        except Exception as e:
            # NOTE: intentionally NOT calling add_log() here -> add_log() calls
            # save_db(), which would recurse. stdout is the only safe sink here.
            print("[DRIVE] DB write failed:", e)


def add_log(level, message, request_id=None):
    entry = {
        "ts": int(time.time() * 1000),
        "level": level,
        "message": f"[{request_id}] {message}" if request_id else message
    }
    DB.setdefault("logs", []).insert(0, entry)
    DB["logs"] = DB["logs"][:400]
    save_db()
    # Mirror to stdout too, so Render's log stream shows it even if the
    # DB write itself is what's failing.
    print(f"[{level.upper()}]", entry["message"])


def new_request_id():
    return secrets.token_hex(4)


def public_state():
    return {
        "bans": DB.get("bans", []),
        "visitors": DB.get("visitors", {})
    }


def decode_data_url(data_url):
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        return None, None
    try:
        head, encoded = data_url.split(",", 1)
        mime = head[5:].split(";", 1)[0] or "application/octet-stream"
        if ";base64" not in head:
            return mime, encoded.encode("utf-8")
        return mime, base64.b64decode(encoded, validate=False)
    except Exception:
        return None, None


class Handler(BaseHTTPRequestHandler):
    server_version = "FreeboxServer/2.0"

    def log_message(self, fmt, *args):
        print("[HTTP]", fmt % args)

    def _cors_headers(self):
        if CORS_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Credentials", "true")

    def do_OPTIONS(self):
        # Only relevant if frontend and backend are on different origins.
        # Same-origin deployments (the default) never trigger a preflight.
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(raw)

    def send_text(self, text, status=200, content_type="text/plain; charset=utf-8"):
        raw = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def token(self):
        auth = self.headers.get("Authorization", "")
        return auth[7:].strip() if auth.startswith("Bearer ") else ""

    def is_admin(self):
        token = self.token()
        return bool(token and secrets.compare_digest(token, ADMIN_CODE))

    def require_admin(self):
        if not self.is_admin():
            self.send_json({"error": "Требуется авторизация администратора"}, 401)
            return False
        return True

    def do_GET(self):
        global DB
        p = urlparse(self.path).path
        q = parse_qs(urlparse(self.path).query)

        if p == "/api/health":
            self.send_json({
                "ok": True,
                "storage": "google-drive" if DRIVE.enabled else "local-json",
                "googleDrive": DRIVE.status(),
                "port": PORT
            })
            return

        if p == "/api/state":
            self.send_json({
                "db": DB,
                "isAdmin": self.is_admin(),
                "clientId": q.get("client_id", [""])[0]
            })
            return

        if p == "/api/admin/drive-status":
            if not self.require_admin():
                return
            self.send_json(DRIVE.status())
            return

        if p == "/api/admin/drive-test":
            if not self.require_admin():
                return
            result = DRIVE.live_test()
            self.send_json(result, 200 if result.get("ok") else 502)
            return

        if p in ("/", "/index.html"):
            index = BASE_DIR / "index.html"
            if not index.exists():
                self.send_text("index.html not found", 404)
                return
            raw = index.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        global DB
        p = urlparse(self.path).path
        body = self.read_json()

        if p == "/api/admin/login":
            code = str(body.get("code", "")).strip()
            current_code = str(DB.get("adminConfig", {}).get("code") or ADMIN_CODE)
            if not code or not secrets.compare_digest(code, current_code):
                self.send_json({"error": "Неверный код администратора"}, 401)
                return
            self.send_json({"ok": True, "token": current_code})
            return

        if p == "/api/admin/logout":
            self.send_json({"ok": True})
            return

        if p == "/api/submissions":
            rid_ = new_request_id()
            sub = body.get("submission")
            if not isinstance(sub, dict):
                self.send_json({"error": "submission должен быть объектом"}, 400)
                return

            ip = str(sub.get("ip", ""))
            add_log("info", f"POST /api/submissions from {ip}, "
                             f"{len(sub.get('attachments') or [])} attachment(s), "
                             f"drive={'connected' if DRIVE.enabled else 'NOT connected'}",
                     rid_)

            if ip in DB.get("bans", []):
                self.send_json({"error": "Пользователь заблокирован"}, 403)
                return

            attachments = []
            drive_errors = []
            for att in sub.get("attachments", []) or []:
                a = dict(att)
                data_url = a.get("dataUrl")
                if not data_url:
                    attachments.append(a)
                    continue

                if not DRIVE.enabled:
                    # Previously this branch did nothing at all: no upload,
                    # no error, no log entry — attachment silently stayed
                    # only as a dataUrl in the local JSON DB. Now it's explicit.
                    a["driveUploaded"] = False
                    a["driveError"] = DRIVE.error or "Google Drive не подключён"
                    drive_errors.append(a["driveError"])
                    attachments.append(a)
                    continue

                mime, raw = decode_data_url(data_url)
                if raw is None:
                    a["driveUploaded"] = False
                    a["driveError"] = "Не удалось декодировать dataUrl"
                    drive_errors.append(a["driveError"])
                    attachments.append(a)
                    continue

                try:
                    t0 = time.time()
                    uploaded = DRIVE.upload_bytes(
                        a.get("name", "file"),
                        mime or a.get("mime"),
                        raw
                    )
                    a["driveFileId"] = uploaded.get("id")
                    a["driveName"] = uploaded.get("name")
                    a["driveUploaded"] = True
                    a["dataUrl"] = data_url
                    add_log("info", f"Drive upload OK: {uploaded.get('name')} "
                                     f"fileId={uploaded.get('id')} "
                                     f"duration={int((time.time()-t0)*1000)}ms", rid_)
                except Exception as e:
                    a["driveUploaded"] = False
                    a["driveError"] = str(e)
                    drive_errors.append(str(e))
                    add_log("err", f"Google Drive upload failed: {e}", rid_)
                attachments.append(a)

            sub["attachments"] = attachments
            DB.setdefault("submissions", []).insert(0, sub)

            visitors = DB.setdefault("visitors", {})
            visitor = visitors.setdefault(
                ip,
                {"count": 0, "first": int(time.time() * 1000), "last": 0}
            )
            visitor["count"] += 1
            visitor["last"] = int(time.time() * 1000)

            save_db()
            add_log("info", f"Submission {sub.get('id')} saved "
                             f"(storage={'google-drive' if DRIVE.enabled else 'local-json'}, "
                             f"drive_errors={len(drive_errors)})", rid_)

            # Text/HTML content itself is always saved (to the local JSON DB,
            # and to the Drive-hosted DB file if Drive is connected) — that part
            # never silently fails. What CAN silently fail is per-attachment
            # upload to Drive, which is now reported honestly below instead of
            # being hidden behind a blanket {"ok": true}.
            status = 200
            self.send_json({
                "ok": True,
                "submissionId": sub.get("id"),
                "publicState": public_state(),
                "storage": DRIVE.status(),
                "driveErrors": drive_errors or None,
                "requestId": rid_
            }, status)
            return

        if p == "/api/admin/regenerate-code":
            if not self.require_admin():
                return
            new_code = secrets.token_hex(4).upper()[:4] + "-" + secrets.token_hex(4).upper()[:4]
            DB["adminConfig"]["code"] = new_code
            save_db()
            self.send_json({"ok": True, "code": new_code})
            return

        self.send_json({"error": "Not found"}, 404)

    def do_PUT(self):
        global DB
        p = urlparse(self.path).path

        if p != "/api/admin/state":
            self.send_json({"error": "Not found"}, 404)
            return

        if not self.require_admin():
            return

        body = self.read_json()
        new_db = body.get("db")

        if not isinstance(new_db, dict):
            self.send_json({"error": "Некорректная база данных"}, 400)
            return

        DB = new_db
        save_db()

        self.send_json({"ok": True, "db": DB})


def main():
    print("=" * 60)
    print(" FREEBOX SERVER")
    print("=" * 60)
    print(f"Listening on http://0.0.0.0:{PORT}")
    print("Google Drive:", "CONNECTED" if DRIVE.enabled else "NOT CONNECTED")
    if DRIVE.error:
        print("Drive info:", DRIVE.error)
    if not DRIVE.enabled:
        print("WARNING: Google Drive is NOT connected. Submissions will only be")
        print("         written to data/database.json on THIS container's disk.")
        print("         On Render's free/standard web services that disk is")
        print("         EPHEMERAL: every redeploy or restart wipes it. Fix the")
        print("         GOOGLE_SERVICE_ACCOUNT_FILE / GOOGLE_DRIVE_FOLDER_ID env")
        print("         vars, or your data WILL be lost.")
    print("=" * 60)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
