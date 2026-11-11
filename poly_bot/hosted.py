"""Hosted multi-user mode: the app on a server, for anyone who signs in with their email.

    python -m poly_bot hosted --port 8090            (behind Caddy, which adds HTTPS: poly.zodypay.com)

No sign-in is needed (owner's choice, 2026-11-09: "no need to signin to use this. just make use freely"): a first
visit becomes a **guest** account tied to that browser (a long-lived cookie). Signing in with an email (a 6-digit
code, no passwords) is optional: it lets a user reach their bots from another browser, and admins need it. A guest
who signs in keeps their bots (moved into the email's account if it already exists). Each user has their own bots in
hosted/users/<id>/profiles/ and sees only those. Paper bots work for everyone. Live bots use the user's own Polymarket
keys (stored encrypted with POLY_SECRET_KEY) and may only be started from a country where Polymarket allows trading
(geo.py checks the user's address, not the server's). Bots that were running when the app stopped start again with it.

Settings, from the environment or the project .env:
    POLY_SECRET_KEY  64 hex characters; encrypts the stored keys (python -m poly_bot.secretbox makes one)
    RESEND_API_KEY   sends the sign-in emails (without it the code is only written to the log: for testing)
    MAIL_FROM        e.g. "Poly Martingale <bot@zodypay.com>" (a domain verified in Resend)
    ADMIN_EMAILS     comma-separated; these users see the user list
    PUBLIC_HOSTS     comma-separated host names the app answers to, e.g. poly.zodypay.com
    MAX_BOTS         bots per user (default 0 = no limit)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Optional

from . import geo
from .instance import ROOT
from .web import App, make_handler

log = logging.getLogger("poly.hosted")

DATA_DIR = ROOT / "hosted"
COOKIE = "poly_session"
SESSION_DAYS = 30
CODE_MINUTES = 10
CODE_TRIES = 5            # wrong codes before a new one must be requested
SEND_GAP = 60             # seconds between two codes to the same email
SENDS_PER_HOUR = 5
CODES_PER_IP_HOUR = 20    # codes asked from one address (any emails), against using the site to spam people
GUEST_DAYS = 365          # a guest's cookie: their bots live as long as it does
GUESTS_PER_IP_HOUR = 20   # new guest accounts from one address
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$")


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def read_dotenv(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and not k.strip().startswith("#"):
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_env() -> None:
    """The project .env fills in what the process environment doesn't set."""
    for k, v in read_dotenv(ROOT / ".env").items():
        os.environ.setdefault(k, v)


def env_list(name: str) -> list[str]:
    return [x.strip().lower() for x in os.environ.get(name, "").split(",") if x.strip()]


# ---------- users, codes, sessions --------------------------------------------------------------------------------

class Store:
    """Users, sign-in codes and sessions in one SQLite file. Codes and session tokens are stored only as hashes."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.db:
            cols = {r["name"]: r["notnull"] for r in self.db.execute("PRAGMA table_info(users)")}
            if cols.get("email"):          # email was NOT NULL before guests existed
                self.db.executescript("""
                    ALTER TABLE users RENAME TO users_old;
                    CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE, created REAL NOT NULL,
                        last_login REAL, disabled INTEGER NOT NULL DEFAULT 0, country TEXT);
                    INSERT INTO users SELECT id, email, created, last_login, disabled, country FROM users_old;
                    DROP TABLE users_old;
                """)
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS guests (ip TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, email TEXT UNIQUE,
                    created REAL NOT NULL, last_login REAL, disabled INTEGER NOT NULL DEFAULT 0, country TEXT);
                CREATE TABLE IF NOT EXISTS codes (email TEXT PRIMARY KEY, hash TEXT NOT NULL, expires REAL NOT NULL,
                    tries INTEGER NOT NULL DEFAULT 0, sent REAL NOT NULL, hour_start REAL NOT NULL,
                    hour_sends INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS code_ips (ip TEXT NOT NULL, sent REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                    created REAL NOT NULL, expires REAL NOT NULL);
            """)

    def user(self, user_id: int) -> Optional[dict]:
        with self.lock:
            row = self.db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def user_by_email(self, email: str) -> Optional[dict]:
        with self.lock:
            row = self.db.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
        return dict(row) if row else None

    def add_user(self, email: str, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO users (email, created) VALUES (?,?)", (email.lower(), now))
            return dict(self.db.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone())

    def new_guest(self, ip: str, now: Optional[float] = None) -> dict:
        """A new guest user (no email), at most GUESTS_PER_IP_HOUR an hour from one address."""
        now = time.time() if now is None else now
        with self.lock, self.db:
            self.db.execute("DELETE FROM guests WHERE created < ?", (now - 3600,))
            if self.db.execute("SELECT COUNT(*) FROM guests WHERE ip=?", (ip,)).fetchone()[0] >= GUESTS_PER_IP_HOUR:
                raise ValueError("Too many new visitors from your network. Try again in an hour.")
            self.db.execute("INSERT INTO guests VALUES (?,?)", (ip, now))
            cur = self.db.execute("INSERT INTO users (email, created, last_login) VALUES (NULL,?,?)", (now, now))
            return dict(self.db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())

    def delete_user(self, user_id: int) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self.db.execute("DELETE FROM users WHERE id=?", (user_id,))

    def set_country(self, user_id: int, country: Optional[str]) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE users SET country=? WHERE id=?", (country, user_id))

    def new_code(self, email: str, ip: str = "", now: Optional[float] = None) -> str:
        """A fresh 6-digit code for `email` (replacing any earlier one), within the sending limits."""
        now = time.time() if now is None else now
        with self.lock, self.db:
            if ip:
                self.db.execute("DELETE FROM code_ips WHERE sent < ?", (now - 3600,))
                if self.db.execute("SELECT COUNT(*) FROM code_ips WHERE ip=?", (ip,)).fetchone()[0] >= CODES_PER_IP_HOUR:
                    raise ValueError("Too many sign-in codes from your network. Try again in an hour.")
            row = self.db.execute("SELECT * FROM codes WHERE email=?", (email,)).fetchone()
            hour_start, sends = now, 0
            if row:
                if now - row["sent"] < SEND_GAP:
                    raise ValueError(f"A code was just sent. Wait {int(SEND_GAP - (now - row['sent'])) + 1} seconds.")
                if now - row["hour_start"] < 3600:
                    hour_start, sends = row["hour_start"], row["hour_sends"]
            if sends >= SENDS_PER_HOUR:
                raise ValueError("Too many codes for this email. Try again in an hour.")
            code = f"{secrets.randbelow(10**6):06d}"
            self.db.execute("REPLACE INTO codes VALUES (?,?,?,?,?,?,?)",
                            (email, _h(email + ":" + code), now + CODE_MINUTES * 60, 0, now, hour_start, sends + 1))
            if ip:
                self.db.execute("INSERT INTO code_ips VALUES (?,?)", (ip, now))
        return code

    def check_code(self, email: str, code: str, now: Optional[float] = None, guest_id: Optional[int] = None) -> dict:
        """The user for a right code, else ValueError. A code works once. A new email becomes a new user, or, when
        `guest_id` is given, that guest's account (the guest keeps their bots)."""
        now = time.time() if now is None else now
        with self.lock:
            row = self.db.execute("SELECT * FROM codes WHERE email=?", (email,)).fetchone()
            if not row or row["expires"] < now or row["tries"] >= CODE_TRIES:
                raise ValueError("This code has expired. Ask for a new one.")
            if not secrets.compare_digest(row["hash"], _h(email + ":" + code.strip())):
                with self.db:             # committed before raising (an error inside the block would roll it back)
                    self.db.execute("UPDATE codes SET tries=tries+1 WHERE email=?", (email,))
                left = CODE_TRIES - row["tries"] - 1
                raise ValueError("Wrong code." + (f" {left} tries left." if left else " Ask for a new one."))
        with self.lock, self.db:
            self.db.execute("DELETE FROM codes WHERE email=?", (email,))
            taken = self.db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone()
            if not taken and guest_id is not None:
                self.db.execute("UPDATE users SET email=? WHERE id=? AND email IS NULL", (email, guest_id))
            self.db.execute("INSERT OR IGNORE INTO users (email, created) VALUES (?,?)", (email, now))
            self.db.execute("UPDATE users SET last_login=? WHERE email=?", (now, email))
            user = dict(self.db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone())
        if user["disabled"]:
            raise ValueError("This account is disabled.")
        return user

    def new_session(self, user_id: int, now: Optional[float] = None, days: int = SESSION_DAYS) -> str:
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            self.db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (_h(token), user_id, now, now + days * 86400))
        return token

    def user_for(self, token: str, now: Optional[float] = None) -> Optional[dict]:
        if not token:
            return None
        now = time.time() if now is None else now
        with self.lock:
            row = self.db.execute("SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                                  "WHERE s.token=? AND s.expires > ? AND u.disabled = 0", (_h(token), now)).fetchone()
        return dict(row) if row else None

    def end_session(self, token: str) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE token=?", (_h(token),))

    def users(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM users ORDER BY id")]

    def set_disabled(self, user_id: int, disabled: bool) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE users SET disabled=? WHERE id=?", (int(disabled), user_id))
            if disabled:
                self.db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


# ---------- email -------------------------------------------------------------------------------------------------

def send_code_email(to: str, code: str) -> None:
    """Send the sign-in code with Resend; without RESEND_API_KEY just log it (testing)."""
    key = os.environ.get("RESEND_API_KEY", "")
    if not key:
        log.warning("RESEND_API_KEY not set: sign-in code for %s is %s", to, code)
        return
    sender = os.environ.get("MAIL_FROM", "Poly Martingale <bot@zodypay.com>")
    text = (f"Your Poly Martingale sign-in code is {code}\n\nIt works for {CODE_MINUTES} minutes. If you didn't ask "
            f"for it, ignore this email.")
    html = (f"<p>Your Poly Martingale sign-in code is</p><p style=\"font-size:28px;font-weight:700;letter-spacing:4px\">"
            f"{code}</p><p>It works for {CODE_MINUTES} minutes. If you didn't ask for it, ignore this email.</p>")
    req = urllib.request.Request(
        "https://api.resend.com/emails", method="POST",
        data=json.dumps({"from": sender, "to": [to], "subject": f"Sign-in code: {code}", "text": text,
                         "html": html}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "poly-martingale-bot"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    except urllib.error.HTTPError as e:
        log.error("Resend refused the email to %s: %s %s", to, e.code, e.read().decode(errors="replace")[:300])
        raise ValueError("Couldn't send the email. Try again later.") from None
    except OSError as e:
        log.error("Resend unreachable: %s", e)
        raise ValueError("Couldn't send the email. Try again later.") from None


# ---------- the users' bots ---------------------------------------------------------------------------------------

class UserApp(App):
    """One user's bots, with a limit on how many."""

    def __init__(self, profiles: Path, user: dict, **kw):
        self.user = user
        self.max_bots = int(os.environ.get("MAX_BOTS", "0"))
        super().__init__(profiles, prefix=f"u{user['id']}/", **kw)


class Hosted:
    def __init__(self, data: Path = DATA_DIR, pm=None):
        self.data = data
        self.store = Store(data / "users.db")
        self.apps: dict[int, UserApp] = {}
        self.pm = pm
        self._lock = threading.Lock()

    def folder(self, user_id: int) -> Path:
        return self.data / "users" / str(user_id) / "profiles"

    def app_for(self, user: dict) -> UserApp:
        with self._lock:
            app = self.apps.get(user["id"])
            if not app:
                kw = {"pm": self.pm} if self.pm else {}
                app = self.apps[user["id"]] = UserApp(self.folder(user["id"]), dict(user), **kw)
            return app

    def is_admin(self, user: Optional[dict]) -> bool:
        return bool(user and user.get("email")) and user["email"].lower() in env_list("ADMIN_EMAILS")

    def sign_in(self, guest: Optional[dict], email: str, code: str) -> dict:
        """Check the code. A guest's account becomes this email's, or (email already has an account) the guest's
        bots move into that account and the guest is removed. Returns the signed-in user."""
        is_guest = bool(guest) and not guest.get("email")
        user = self.store.check_code(email, code, guest_id=guest["id"] if is_guest else None)
        if is_guest:
            if user["id"] == guest["id"]:
                app = self.apps.get(user["id"])
                if app:
                    app.user["email"] = user["email"]
            else:
                self._move_bots(guest["id"], user)
        return user

    def _move_bots(self, from_id: int, to: dict) -> None:
        with self._lock:
            old = self.apps.pop(from_id, None)
        running = set()
        if old:
            running = {c.id for c in old.controllers.values() if c.running}
            old.stop_all()
            for c in old.controllers.values():
                c.lock.close()
                for hd in list(c.log.handlers):
                    c.log.removeHandler(hd)
                    hd.close()
        src, target = self.folder(from_id), self.app_for(to)
        if src.is_dir():
            from .instance import free_bot_id
            from .web import Controller
            for folder in sorted(p for p in src.iterdir() if p.is_dir() and (p / "config.json").exists()):
                new_id = free_bot_id(folder.name, target.profiles, target.controllers)
                shutil.move(str(folder), str(target.profiles / new_id))
                ctl = target.controllers[new_id] = Controller(new_id, target.profiles / new_id, target.pm,
                                                              target.prefix + new_id)
                if folder.name in running:
                    try:
                        ctl.start()
                    except Exception as e:  # noqa: BLE001
                        ctl.log.warning("Couldn't start again after moving accounts: %s", e)
            shutil.rmtree(self.data / "users" / str(from_id), ignore_errors=True)
        self.store.delete_user(from_id)
        log.info("Guest %s signed in as user %s; their bots moved over", from_id, to["id"])

    def request_code(self, body: dict, ip: str = "") -> dict:
        email = str(body.get("email") or "").strip().lower()
        if not EMAIL_RE.match(email):
            raise ValueError("Enter a valid email address.")
        send_code_email(email, self.store.new_code(email, ip))
        return {"ok": True}

    def admin_users(self) -> dict:
        out = []
        for u in self.store.users():
            app = self.apps.get(u["id"])
            bots = list(app.controllers.values()) if app else []
            out.append({"id": u["id"], "email": u["email"] or "", "created": u["created"], "last_login": u["last_login"],
                        "country": u.get("country") or "", "disabled": bool(u["disabled"]), "bots": len(bots),
                        "running": sum(c.running for c in bots),
                        "live_running": sum(c.running and c.cfg.is_live for c in bots)})
        return {"users": out}

    def admin_disable(self, user_id: int, disabled: bool) -> dict:
        app = self.apps.get(user_id)
        if disabled and app:
            app.stop_all()
            for c in app.controllers.values():          # a disabled user's bots don't start again with the app
                (c.folder / ".running").unlink(missing_ok=True)
        self.store.set_disabled(user_id, disabled)
        log.info("User %s %s", user_id, "disabled" if disabled else "enabled")
        return self.admin_users()

    def adopt(self, email: str, profiles: Path) -> int:
        """Move the bots of the single-owner app (profiles/) into `email`'s account. Returns how many moved."""
        user = self.store.add_user(email)
        target = self.folder(user["id"])
        target.mkdir(parents=True, exist_ok=True)
        moved = 0
        if profiles.is_dir():
            for folder in sorted(p for p in profiles.iterdir() if p.is_dir() and (p / "config.json").exists()):
                dest = target / folder.name
                if dest.exists():
                    continue
                shutil.move(str(folder), str(dest))
                moved += 1
        return moved

    def resume_all(self) -> None:
        for u in self.store.users():
            if not u["disabled"]:
                self.app_for(u).resume()

    def stop_all(self) -> None:
        for app in self.apps.values():
            app.stop_all()


def make_hosted_handler(h: Hosted):
    base = make_handler(None)
    hosts = set(env_list("PUBLIC_HOSTS")) | {"127.0.0.1", "localhost"}
    secure = os.environ.get("COOKIE_INSECURE") != "1"       # tests on plain http://localhost set COOKIE_INSECURE=1
    admin_path = re.compile(r"^/api/admin/users/(\d+)/(disable|enable)$")

    class Handler(base):
        def setup(self):
            super().setup()
            self.headers_out: list[tuple[str, str]] = []

        def _token(self) -> str:
            c = SimpleCookie(self.headers.get("Cookie") or "")
            return c[COOKIE].value if COOKIE in c else ""

        @property
        def user(self) -> Optional[dict]:
            if not hasattr(self, "_user"):
                self._user = h.store.user_for(self._token())
            return self._user

        def _app(self):
            return h.app_for(self.user) if self.user else None

        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "").split(":")[0].lower() in hosts

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            for k, v in self.headers_out:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _cookie(self, token: str, max_age: int) -> None:
            self.headers_out.append(("Set-Cookie", f"{COOKIE}={token}; Path=/; Max-Age={max_age}; HttpOnly; "
                                                   f"SameSite=Lax{'; Secure' if secure else ''}"))

        def _page(self) -> bytes:
            return super()._page().replace(b'<html lang="en">', b'<html lang="en" data-hosted="1">', 1)

        def _ip(self) -> str:
            # behind Caddy the client's address is the last X-Forwarded-For entry (Caddy appends it); directly,
            # the socket's. The app listens on 127.0.0.1 only, so only Caddy can set the header.
            fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
            return fwd if fwd and self.client_address[0] in ("127.0.0.1", "::1") else self.client_address[0]

        def _live_check(self) -> tuple[bool, str]:
            """geo.live_allowed for this visitor, with a clearer reason for a visit through localhost (an SSH or
            VS Code tunnel to the server): there the visitor's country can't be seen at all."""
            ip = self._ip()
            ok, why = geo.live_allowed(ip)
            if not ok and ip in ("127.0.0.1", "::1"):
                hosts = env_list("PUBLIC_HOSTS")
                site = f"https://{hosts[0]}" if hosts else "the public address"
                why = (f"You opened the app through localhost (a tunnel to the server), so your country can't be "
                       f"seen. Open {site} to start or check live bots. Paper trading works here.")
            return ok, why

        def _live_refusal(self, app, bot_id: str) -> str:
            try:
                ctl = app.get(bot_id)
            except LookupError:
                return ""
            if not ctl.cfg.is_live:
                return ""
            ok, why = self._live_check()
            h.store.set_country(self.user["id"], why if ok else geo.DB.country(self._ip()))
            if not ok:
                log.info("User %s: live bot %s refused from %s", self.user["id"], bot_id, self._ip())
            return "" if ok else why

        def do_GET(self):
            if self._host_ok() and self.path.split("?")[0] == "/api/me":
                u = self.user
                if not u:                     # a first visit: a guest account, no email needed
                    try:
                        u = self._user = h.store.new_guest(self._ip())
                    except ValueError as e:
                        return self._json(429, {"error": str(e)})
                    self._cookie(h.store.new_session(u["id"], days=GUEST_DAYS), GUEST_DAYS * 86400)
                    log.info("New guest %s", u["id"])
                ok, why = self._live_check()
                return self._json(200, {"email": u["email"] or "", "guest": not u["email"], "admin": h.is_admin(u),
                                        "live_allowed": ok,
                                        "live_reason": "" if ok else why, "country": geo.DB.country(self._ip()) or ""})
            if self._host_ok() and self.path == "/api/admin/users":
                if not h.is_admin(self.user):
                    return self._json(403, {"error": "forbidden"})
                return self._call(h.admin_users)
            return super().do_GET()

        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-Requested-With") != "bot-ui":
                return self._json(403, {"error": "forbidden"})
            if self.path.startswith("/api/auth/"):
                return self._auth()
            m = admin_path.match(self.path)
            if m:
                if not h.is_admin(self.user):
                    return self._json(403, {"error": "forbidden"})
                return self._call(h.admin_disable, int(m.group(1)), m.group(2) == "disable")
            return super().do_POST()

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except ValueError:
                return {}
            return body if isinstance(body, dict) else {}

        def _auth(self):
            body = self._body()
            if self.path == "/api/auth/code":
                return self._call(h.request_code, body, self._ip())
            if self.path == "/api/auth/verify":
                email = str(body.get("email") or "").strip().lower()
                try:
                    user = h.sign_in(self.user, email, str(body.get("code") or ""))
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                if self._token():
                    h.store.end_session(self._token())
                self._cookie(h.store.new_session(user["id"]), SESSION_DAYS * 86400)
                log.info("User %s signed in", user["id"])
                return self._json(200, {"ok": True, "email": user["email"]})
            if self.path == "/api/auth/logout":
                if self._token():
                    h.store.end_session(self._token())
                self._cookie("", 0)
                return self._json(200, {"ok": True})
            return self._json(404, {"error": "not found"})

    return Handler


def serve_hosted(host: str, port: int, adopt_email: str = "") -> None:
    from .instance import PROFILES_DIR, bind_server
    if not os.environ.get("POLY_SECRET_KEY"):
        raise SystemExit("POLY_SECRET_KEY is not set: it encrypts users' keys. Make one with "
                         "`python -m poly_bot.secretbox` and put it in the project .env.")
    h = Hosted()
    if adopt_email:
        moved = h.adopt(adopt_email, PROFILES_DIR)
        if moved:
            log.info("Moved %d bot(s) from profiles/ into %s's account", moved, adopt_email)
    threading.Thread(target=lambda: geo.DB.country("1.1.1.1"), daemon=True).start()   # load the country database
    for u in h.store.users():
        if not u["disabled"]:
            h.app_for(u)
    h.resume_all()
    server = bind_server(host, port, make_hosted_handler(h), tries=1)
    log.info("Hosted app on http://%s:%d for %s (%d users)", host, server.server_address[1],
             ", ".join(env_list("PUBLIC_HOSTS")) or "localhost", len(h.apps))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        h.stop_all()
        server.server_close()
