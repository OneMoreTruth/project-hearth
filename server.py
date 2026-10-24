"""Project Hearth local media host. Standard-library only for the first milestone."""
from __future__ import annotations

import json
import hashlib
import hmac
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import threading
import urllib.parse
import uuid
from http.cookies import SimpleCookie
import discovery
import torbox
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
if getattr(sys, "frozen", False):
    DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Project Hearth" / "data"
else:
    DATA = ROOT / "data"
DB = DATA / "hearth.sqlite3"
MEDIA_EXTS = {".mkv", ".mp4", ".m4v", ".mov", ".avi", ".webm", ".mpg", ".mpeg", ".ts"}
SCANS: dict[int, dict] = {}
LOCK = threading.Lock()
LOCAL_HOST = None
ACTIVE_SESSION: str | None = None
AUTH_LOCK = threading.Lock()

@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS libraries(id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('movies','tv')), path TEXT NOT NULL UNIQUE, added_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS media(id INTEGER PRIMARY KEY, library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE, path TEXT NOT NULL UNIQUE, title TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('movie','episode')), series TEXT, season INTEGER, episode INTEGER, size INTEGER NOT NULL, modified REAL NOT NULL, duration REAL, scanned_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS media_title_idx ON media(title COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS media_library_idx ON media(library_id, kind, series, season, episode);
        CREATE TABLE IF NOT EXISTS progress(media_id INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE, position REAL NOT NULL DEFAULT 0, duration REAL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS scan_jobs(id INTEGER PRIMARY KEY, library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE, status TEXT NOT NULL, discovered INTEGER NOT NULL DEFAULT 0, indexed INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, finished_at TEXT);
        CREATE TABLE IF NOT EXISTS local_account(id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL UNIQUE, salt BLOB NOT NULL, password_hash BLOB NOT NULL, created_at TEXT NOT NULL);
        """)
        c.execute("INSERT OR IGNORE INTO preferences(key,value) VALUES('product_name','Project Hearth')")
        c.execute("INSERT OR IGNORE INTO preferences(key,value) VALUES('accent','#e7a86b')")

def _session_file() -> Path:
    return DATA.parent / "account.session"

def restore_saved_session() -> None:
    global ACTIVE_SESSION
    path = _session_file()
    try:
        with db() as conn:
            account = conn.execute("SELECT username FROM local_account WHERE id=1").fetchone()
        if not account or not path.exists(): return
        from torbox import _protect
        token = _protect(path.read_bytes(), decrypt=True).decode("ascii")
        with AUTH_LOCK: ACTIVE_SESSION = token
    except Exception:
        with AUTH_LOCK: ACTIVE_SESSION = None

def auth_status() -> dict:
    with db() as conn:
        account = conn.execute("SELECT username FROM local_account WHERE id=1").fetchone()
    with AUTH_LOCK: signed_in = bool(ACTIVE_SESSION)
    return {"configured": bool(account), "authenticated": signed_in, "username": account["username"] if account else None, "remembered": signed_in and _session_file().exists()}

def create_local_account(username: str, password: str) -> None:
    username = username.strip()
    if len(username) < 3 or len(username) > 40: raise ValueError("Choose a username between 3 and 40 characters.")
    if len(password) < 8: raise ValueError("Use a password with at least 8 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    with db() as conn:
        if conn.execute("SELECT 1 FROM local_account WHERE id=1").fetchone(): raise ValueError("A local Hearth account already exists on this PC.")
        conn.execute("INSERT INTO local_account(id,username,salt,password_hash,created_at) VALUES(1,?,?,?,?)", (username,salt,digest,now()))

def login_local_account(username: str, password: str, remember: bool = False) -> str:
    global ACTIVE_SESSION
    with db() as conn: account = conn.execute("SELECT username,salt,password_hash FROM local_account WHERE id=1").fetchone()
    if not account or account["username"].casefold() != username.strip().casefold(): raise ValueError("Username or password is incorrect.")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), account["salt"], 310_000)
    if not hmac.compare_digest(digest, account["password_hash"]): raise ValueError("Username or password is incorrect.")
    token = secrets.token_urlsafe(32)
    with AUTH_LOCK: ACTIVE_SESSION = token
    if remember:
        from torbox import _protect
        _session_file().parent.mkdir(parents=True, exist_ok=True)
        _session_file().write_bytes(_protect(token.encode("ascii")))
    else:
        _session_file().unlink(missing_ok=True)
    return token

def logout_local_account() -> None:
    global ACTIVE_SESSION
    with AUTH_LOCK: ACTIVE_SESSION = None
    _session_file().unlink(missing_ok=True)

def clean_title(filename: str) -> str:
    s = Path(filename).stem
    s = re.sub(r"[._]", " ", s)
    s = re.sub(r"\s*\[[^]]*\]", "", s)
    s = re.sub(r"\s*\([^)]*\)", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s+(?:19|20)\d{2}.*$", "", s).strip() or Path(filename).stem

def scan_library(library_id: int):
    with db() as c:
        lib = c.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
        if not lib: return
        jid = c.execute("INSERT INTO scan_jobs(library_id,status,started_at) VALUES(?,?,?)", (library_id,"running",now())).lastrowid
    with LOCK: SCANS[library_id] = {"id": jid, "status":"running", "discovered":0,"indexed":0,"errors":0}
    root = Path(lib["path"])
    try:
        canonical = root.resolve(strict=True)
        if not canonical.is_dir(): raise OSError("Selected path is not a folder")
        found = []
        for current, dirs, files in os.walk(canonical, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(current)/d).is_symlink() and d not in {".git", "node_modules", "@eaDir"}]
            for name in files:
                p = Path(current)/name
                if p.suffix.lower() in MEDIA_EXTS and not p.is_symlink(): found.append(p)
            with LOCK: SCANS[library_id]["discovered"] = len(found)
        with db() as c:
            existing = {r["path"] for r in c.execute("SELECT path FROM media WHERE library_id=?", (library_id,))}
            seen = set()
            for p in found:
                try:
                    stat = p.stat()
                    media_path = str(p.resolve(strict=True))
                    relparts = p.relative_to(canonical).parts
                    match = re.search(r"(?i)\bS(\d{1,2})\s*E(\d{1,3})\b", p.name)
                    match2 = re.search(r"(?i)\b(\d{1,2})x(\d{1,3})\b", p.name)
                    ep = match or match2
                    if lib["kind"] == "tv" and ep:
                        series = relparts[0] if len(relparts)>1 else clean_title(p.name)
                        title = clean_title(re.split(r"(?i)\bS\d{1,2}\s*E\d{1,3}\b|\b\d{1,2}x\d{1,3}\b", Path(p.name).stem)[-1]).strip(" -_") or f"Episode {ep.group(2)}"
                        kind, season, episode = "episode", int(ep.group(1)), int(ep.group(2))
                    else:
                        title, series, season, episode, kind = clean_title(p.name), None, None, None, "movie"
                    c.execute("INSERT INTO media(library_id,path,title,kind,series,season,episode,size,modified,scanned_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET library_id=excluded.library_id,title=excluded.title,kind=excluded.kind,series=excluded.series,season=excluded.season,episode=excluded.episode,size=excluded.size,modified=excluded.modified,scanned_at=excluded.scanned_at", (library_id,media_path,title,kind,series,season,episode,stat.st_size,stat.st_mtime,now()))
                    seen.add(media_path)
                    with LOCK: SCANS[library_id]["indexed"] += 1
                except Exception:
                    with LOCK: SCANS[library_id]["errors"] += 1
            for missing in existing - seen:
                c.execute("DELETE FROM media WHERE library_id=? AND path=?", (library_id, missing))
            c.execute("UPDATE scan_jobs SET status='complete',discovered=?,indexed=?,errors=?,finished_at=? WHERE id=?", (len(found),SCANS[library_id]["indexed"],SCANS[library_id]["errors"],now(),jid))
    except Exception as e:
        with LOCK: SCANS[library_id]["status"] = "error"; SCANS[library_id]["message"] = str(e)
        with db() as c: c.execute("UPDATE scan_jobs SET status='error',finished_at=? WHERE id=?",(now(),jid))
    else:
        with LOCK: SCANS[library_id]["status"] = "complete"

def now(): return datetime.now(timezone.utc).isoformat()

class Handler(BaseHTTPRequestHandler):
    server_version = "Hearth/0.1"
    def log_message(self, *_): pass
    def send_json(self, data, status=200, extra_headers=None):
        raw=json.dumps(data).encode(); self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(raw))); self.send_header("Cache-Control","no-store")
        for key,value in extra_headers or []: self.send_header(key,value)
        self.end_headers(); self.wfile.write(raw)
    def authorized(self):
        try:
            cookie=SimpleCookie(); cookie.load(self.headers.get("Cookie", "")); presented=cookie.get("hearth_session")
            with AUTH_LOCK: return bool(presented and ACTIVE_SESSION and hmac.compare_digest(presented.value, ACTIVE_SESSION))
        except Exception: return False
    def body(self):
        n=int(self.headers.get("Content-Length",0)); return json.loads(self.rfile.read(n) or b"{}")
    def do_GET(self):
        u=urllib.parse.urlparse(self.path); path=urllib.parse.unquote(u.path)
        if path == "/":
            raw=(RESOURCE_ROOT/"index.html").read_bytes(); self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if path.startswith("/api/"):
            if path == "/api/auth/status":
                status=auth_status(); headers=[]
                with AUTH_LOCK: token=ACTIVE_SESSION
                if token: headers=[("Set-Cookie",f"hearth_session={token}; Path=/; HttpOnly; SameSite=Strict")]
                self.send_json(status,extra_headers=headers); return
            if not self.authorized(): self.send_json({"error":"Sign in to your local Hearth account."},401); return
            if path == "/api/discovery/settings":
                self.send_json(discovery.settings()); return
            if path == "/api/discovery/search":
                query = urllib.parse.parse_qs(u.query)
                try: self.send_json({"items": discovery.tmdb_search(query.get("q", [""])[0], query.get("type", ["movie"])[0])})
                except Exception as e: self.send_json({"error":str(e)},400)
                return
            if path == "/api/discovery/episodes":
                query = urllib.parse.parse_qs(u.query)
                try:
                    season = query.get("season", [None])[0]
                    self.send_json(discovery.tmdb_tv_details(query.get("id", [""])[0], int(season) if season not in (None, "") else None))
                except Exception as e: self.send_json({"error":str(e)},400)
                return
            if path == "/api/discovery/title":
                query = urllib.parse.parse_qs(u.query)
                try:
                    season = query.get("season", [None])[0]; episode = query.get("episode", [None])[0]
                    self.send_json(discovery.title_sources(query.get("id", [""])[0], query.get("type", ["movie"])[0], query.get("imdb", [None])[0], int(season) if season else None, int(episode) if episode else None))
                except Exception as e: self.send_json({"error":str(e)},400)
                return
            with db() as c:
                if path=="/api/overview":
                    libs=[dict(r) for r in c.execute("SELECT l.*,count(m.id) AS item_count FROM libraries l LEFT JOIN media m ON m.library_id=l.id GROUP BY l.id ORDER BY l.id")]
                    items=[dict(r) for r in c.execute("SELECT m.*,coalesce(p.position,0) AS position,coalesce(p.duration,m.duration) AS progress_duration FROM media m LEFT JOIN progress p ON p.media_id=m.id ORDER BY m.title COLLATE NOCASE LIMIT 500")]
                    prefs={r["key"]:r["value"] for r in c.execute("SELECT * FROM preferences")}
                    self.send_json({"libraries":libs,"items":items,"preferences":prefs}); return
                if path.startswith("/api/scan/"):
                    lid=int(path.rsplit("/",1)[-1]);
                    with LOCK: s=SCANS.get(lid)
                    if s: self.send_json(s); return
                    row=c.execute("SELECT * FROM scan_jobs WHERE library_id=? ORDER BY id DESC LIMIT 1",(lid,)).fetchone(); self.send_json(dict(row) if row else {"status":"idle"}); return
                if path.startswith("/api/media/"):
                    mid=int(path.rsplit("/",1)[-1]); row=c.execute("SELECT path FROM media WHERE id=?",(mid,)).fetchone()
                    if not row: self.send_error(404); return
                    self.stream(Path(row["path"])); return
            self.send_error(404); return
        self.send_error(404)
    def stream(self,p):
        try: resolved=p.resolve(strict=True); size=resolved.stat().st_size
        except OSError: self.send_error(404,"Media file is unavailable"); return
        # Only serve files currently indexed in the database.
        with db() as c:
            if not c.execute("SELECT 1 FROM media WHERE path=?",(str(resolved),)).fetchone(): self.send_error(403); return
        start,end=0,size-1; rng=self.headers.get("Range")
        if rng:
            m=re.match(r"bytes=(\d*)-(\d*)",rng)
            if not m: self.send_error(416); return
            if m.group(1): start=int(m.group(1))
            if m.group(2): end=int(m.group(2))
            if not m.group(1): start=max(0,size-int(m.group(2)))
            end=min(end,size-1)
            if start>end or start>=size: self.send_response(416); self.send_header("Content-Range",f"bytes */{size}"); self.end_headers(); return
        self.send_response(206 if rng else 200); self.send_header("Content-Type",mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"); self.send_header("Accept-Ranges","bytes"); self.send_header("Content-Length",str(end-start+1))
        if rng: self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with resolved.open("rb") as f:
                f.seek(start); remaining=end-start+1
                while remaining:
                    chunk=f.read(min(1024*1024,remaining))
                    if not chunk: break
                    self.wfile.write(chunk); remaining-=len(chunk)
        except (BrokenPipeError,ConnectionResetError): pass
    def do_POST(self):
        path=urllib.parse.urlparse(self.path).path
        if path == "/api/shutdown":
            self.send_json({"stopping":True})
            host = LOCAL_HOST
            if host: threading.Thread(target=host.shutdown, daemon=True).start()
            return
        try: data=self.body()
        except Exception: self.send_json({"error":"Invalid JSON body"},400); return
        if path == "/api/auth/create":
            try:
                create_local_account(data.get("username", ""), data.get("password", ""))
                token=login_local_account(data["username"], data["password"], bool(data.get("remember", True)))
                self.send_json({"authenticated":True,"username":data["username"].strip()},201,[("Set-Cookie",f"hearth_session={token}; Path=/; HttpOnly; SameSite=Strict")])
            except Exception as e: self.send_json({"error":str(e)},400)
            return
        if path == "/api/auth/login":
            try:
                token=login_local_account(data.get("username", ""), data.get("password", ""), bool(data.get("remember", False)))
                self.send_json({"authenticated":True,"username":data.get("username", "").strip()},extra_headers=[("Set-Cookie",f"hearth_session={token}; Path=/; HttpOnly; SameSite=Strict")])
            except Exception as e: self.send_json({"error":str(e)},401)
            return
        if path == "/api/auth/logout":
            logout_local_account()
            self.send_json({"authenticated":False},extra_headers=[("Set-Cookie","hearth_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")])
            return
        if path.startswith("/api/") and not self.authorized():
            self.send_json({"error":"Sign in to your local Hearth account."},401); return
        if path=="/api/pick-folder":
            try:
                import tkinter as tk
                from tkinter import filedialog
                app=tk.Tk(); app.withdraw(); app.attributes("-topmost",True); selected=filedialog.askdirectory(parent=app); app.destroy()
                self.send_json({"path":selected or ""})
            except Exception as e: self.send_json({"error":f"Native folder picker unavailable: {e}"},501)
            return
        if path=="/api/libraries":
            p=data.get("path","").strip(); kind=data.get("kind"); name=data.get("name","").strip()
            if kind not in ("movies","tv") or not p or not name: self.send_json({"error":"Name, folder, and library type are required"},400); return
            try:
                canonical=str(Path(p).expanduser().resolve(strict=True))
                if not Path(canonical).is_dir(): raise ValueError("Path must be a folder")
                with db() as c: lid=c.execute("INSERT INTO libraries(name,kind,path,added_at) VALUES(?,?,?,?)",(name,kind,canonical,now())).lastrowid
            except sqlite3.IntegrityError: self.send_json({"error":"That folder is already in a library"},409); return
            except Exception as e: self.send_json({"error":str(e)},400); return
            threading.Thread(target=scan_library,args=(lid,),daemon=True).start(); self.send_json({"id":lid},201); return
        if path.startswith("/api/libraries/") and path.endswith("/scan"):
            lid=int(path.split("/")[3])
            with db() as c:
                if not c.execute("SELECT 1 FROM libraries WHERE id=?",(lid,)).fetchone(): self.send_json({"error":"Library not found"},404); return
            with LOCK:
                if SCANS.get(lid,{}).get("status")=="running": self.send_json({"status":"running"}); return
            threading.Thread(target=scan_library,args=(lid,),daemon=True).start(); self.send_json({"status":"started"},202); return
        if path=="/api/progress":
            try:
                mid=int(data["media_id"]); pos=max(0,float(data["position"])); dur=max(0,float(data.get("duration") or 0))
                with db() as c:
                    if not c.execute("SELECT 1 FROM media WHERE id=?",(mid,)).fetchone(): self.send_json({"error":"Media not found"},404); return
                    c.execute("INSERT INTO progress(media_id,position,duration,updated_at) VALUES(?,?,?,?) ON CONFLICT(media_id) DO UPDATE SET position=excluded.position,duration=excluded.duration,updated_at=excluded.updated_at",(mid,pos,dur,now()))
                self.send_json({"saved":True})
            except Exception as e: self.send_json({"error":str(e)},400)
            return
        if path=="/api/preferences":
            with db() as c:
                for k,v in data.items():
                    if k in ("product_name","accent") and isinstance(v,str) and len(v)<80: c.execute("INSERT INTO preferences(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,v))
            self.send_json({"saved":True}); return
        if path == "/api/discovery/settings":
            try:
                discovery.save_settings(tmdb_key=data.get("tmdb_key"))
                if "torbox_key" in data:
                    if data["torbox_key"].strip(): torbox.save_token(data["torbox_key"].strip())
                    else: torbox.clear_token()
                self.send_json({"saved":True, **discovery.settings()})
            except Exception as e: self.send_json({"error":str(e)},400)
            return
        if path == "/api/discovery/addons":
            try:
                if data.get("remove"):
                    discovery.remove_addon(data["remove"])
                    self.send_json({"saved":True, **discovery.settings()})
                else:
                    addon = discovery.add_addon(data.get("manifest", ""))
                    self.send_json({"saved":True, "addon":addon, **discovery.settings()},201)
            except Exception as e: self.send_json({"error":str(e)},400)
            return
        if path == "/api/discovery/debridio":
            try:
                addon = discovery.add_debridio(data.get("api_key_or_manifest", ""))
                self.send_json({"saved":True, "addon":addon, **discovery.settings()},201)
            except Exception as e: self.send_json({"error":str(e)},400)
            return
        if path == "/api/discovery/resolve":
            try:
                token = torbox.load_token()
                if not token: raise RuntimeError("Connect your TorBox account in the desktop app before playing torrent streams.")
                url = data.get("url")
                if isinstance(url, str) and url.startswith("https://"):
                    self.send_json({"url":url}); return
                info_hash = data.get("infoHash") or data.get("hash")
                if not info_hash: raise RuntimeError("This add-on result does not have a supported playable URL or torrent hash.")
                url = torbox.resolve_hash(token, info_hash, data.get("title", ""), data.get("fileIdx"))
                self.send_json({"url":url})
            except Exception as e: self.send_json({"error":str(e)},400)
            return
        self.send_error(404)

if __name__=="__main__":
    import argparse, webbrowser
    parser=argparse.ArgumentParser(description="Project Hearth personal media host")
    parser.add_argument("--port",type=int,default=8765); parser.add_argument("--no-open",action="store_true")
    args=parser.parse_args(); init_db(); server=ThreadingHTTPServer(("127.0.0.1",args.port),Handler)
    print(f"Project Hearth running at http://127.0.0.1:{args.port} (local machine only)")
    if not args.no_open: threading.Timer(.8,lambda:webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nShutting down…")
    finally: server.shutdown(); server.server_close()
