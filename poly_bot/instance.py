"""Running several bots side by side: bot folders, a single-instance lock per bot folder, and free-port selection.

Every bot is a folder under profiles/<id>/ with its own config.json, state, trade log, paper account and bot.log.
All bots are equal; the project folder holds no bot.
"""
from __future__ import annotations

import errno
import os
import re
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import IO

ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT / "profiles"
PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,29}$")
RESERVED = {"app"}           # the log tag for lines from no bot in particular


def slug(name: str) -> str:
    """'BTC 5m fast' -> 'btc-5m-fast' (a folder name)."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30].strip("-")
    return s if s and PROFILE_NAME.match(s) and s not in RESERVED else "bot"


def free_bot_id(base: str, profiles_dir: Path, taken=()) -> str:
    """`base`, or base-2, base-3, ... if a bot (or a folder) already has that id."""
    bot_id, n = base, 2
    while bot_id in RESERVED or bot_id in taken or (profiles_dir / bot_id).exists():
        bot_id = f"{base[:27]}-{n}"
        n += 1
    return bot_id


def bot_ids(profiles_dir: Path = PROFILES_DIR) -> list[str]:
    """Every bot folder (profiles/<id>/ with a config.json), sorted."""
    if not profiles_dir.is_dir():
        return []
    return [d.name for d in sorted(profiles_dir.iterdir())
            if d.is_dir() and PROFILE_NAME.match(d.name) and d.name not in RESERVED and (d / "config.json").exists()]


class AlreadyRunning(RuntimeError):
    pass


def acquire_lock(folder: Path) -> IO:
    """Hold an exclusive lock on <folder>/.lock for the life of the process (the returned file must stay open).

    Two bots sharing one data folder would overwrite each other's progress and trade log. The OS releases the lock
    when the process exits, even after a crash, so there's nothing stale to clean up.
    """
    f = open(folder / ".lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise AlreadyRunning(f"A bot is already running from {folder}. Stop it in the app (or close the other "
                             f"window) first.")
    return f


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind a port that's already in use, with requests going to
    # either one. Only reuse addresses where it just means "restart quickly after a crash".
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


_IN_USE = {errno.EADDRINUSE, errno.EACCES, 10048, 10013}   # 10048/10013: Windows "in use" / "access denied"


def bind_server(host: str, port: int, handler, tries: int = 20) -> ThreadingHTTPServer:
    """The HTTP server on `port`, or on the next free port after it."""
    for p in range(port, port + tries):
        try:
            return _Server((host, p), handler)
        except OSError as e:
            if e.errno not in _IN_USE and getattr(e, "winerror", None) not in _IN_USE:
                raise
    raise OSError(f"No free port between {port} and {port + tries - 1}")
