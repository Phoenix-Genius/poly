"""Where a visitor is, for Polymarket's location rules (hosted mode).

Polymarket rejects orders from restricted places and asks apps that trade for users to check their location
(docs.polymarket.com/api-reference/geoblock). The hosted app trades from its own server, so it must check where each
*user* is, not where the server is. The country comes from DB-IP's free "IP to Country Lite" database
(data/dbip-country-lite.csv.gz, CC BY 4.0, attribution on the page), so users' addresses are never sent to anyone.

The database knows countries, not provinces, so where Polymarket restricts only some regions (Canada: BC, ON, AB,
QC; Ukraine: Crimea, Donetsk, Luhansk) the whole country is blocked. An address the database doesn't know is blocked
too. Japan, South Korea, Ireland, Netherlands and Malta are restricted only on Polymarket's own website (the API is
open there); the owner chose to allow them (2026-11-09).
"""
from __future__ import annotations

import bisect
import csv
import gzip
import ipaddress
import threading
from pathlib import Path
from typing import Optional

from .instance import ROOT

DB_PATH = ROOT / "data" / "dbip-country-lite.csv.gz"
DB_URL = "https://download.db-ip.com/free/dbip-country-lite-{month}.csv.gz"   # month = YYYY-MM, new each month

# Polymarket's restricted jurisdictions as of 2026-11-09: "block completely" (OFAC) and "close-only on frontend
# and API". Region-level entries (CA-BC, UA-43, ...) are listed by their country.
BLOCKED = {
    "IR": "Iran", "SY": "Syria", "CU": "Cuba", "KP": "North Korea", "UA": "Ukraine",
    "AR": "Argentina", "AU": "Australia", "BY": "Belarus", "BE": "Belgium", "BI": "Burundi", "BR": "Brazil",
    "CA": "Canada", "CF": "Central African Republic", "CD": "Congo (DRC)", "ET": "Ethiopia", "FR": "France",
    "DE": "Germany", "IQ": "Iraq", "IT": "Italy", "LB": "Lebanon", "LY": "Libya", "MM": "Myanmar",
    "NZ": "New Zealand", "NI": "Nicaragua", "PL": "Poland", "RU": "Russia", "SG": "Singapore", "SO": "Somalia",
    "SK": "Slovakia", "SS": "South Sudan", "SD": "Sudan", "TW": "Taiwan", "TH": "Thailand", "GB": "United Kingdom",
    "US": "United States", "UM": "U.S. Minor Outlying Islands", "VE": "Venezuela", "YE": "Yemen", "ZW": "Zimbabwe",
}


class CountryDB:
    """IP -> country code, from the DB-IP CSV (start,end,country), loaded on first use."""

    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._v4: Optional[tuple[list, list, list]] = None
        self._v6: Optional[tuple[list, list, list]] = None

    def _load(self) -> None:
        v4, v6 = ([], [], []), ([], [], [])
        with gzip.open(self.path, "rt", newline="") as f:
            for start, end, cc in csv.reader(f):
                a, b = ipaddress.ip_address(start), ipaddress.ip_address(end)
                t = v4 if a.version == 4 else v6
                t[0].append(int(a))
                t[1].append(int(b))
                t[2].append(cc)
        self._v4, self._v6 = v4, v6

    def country(self, ip: str) -> Optional[str]:
        """"JP", "US", ... or None when unknown (or the database is missing)."""
        try:
            addr = ipaddress.ip_address(ip.strip())
        except ValueError:
            return None
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        if addr.is_private or addr.is_loopback:
            return None
        with self._lock:
            if self._v4 is None:
                if not self.path.exists():
                    return None
                self._load()
        starts, ends, ccs = self._v4 if addr.version == 4 else self._v6
        i = bisect.bisect_right(starts, int(addr)) - 1
        if i >= 0 and int(addr) <= ends[i] and ccs[i] not in ("ZZ", ""):
            return ccs[i]
        return None


DB = CountryDB()


def live_allowed(ip: str, db: Optional[CountryDB] = None) -> tuple[bool, str]:
    """(may this visitor run live bots?, a plain-words reason or the country code)."""
    cc = (db or DB).country(ip)
    if not cc:
        return False, ("We couldn't tell which country you are in, so live trading is off. "
                       "Paper trading works everywhere.")
    if cc in BLOCKED:
        return False, (f"Polymarket doesn't allow trading from {BLOCKED[cc]}, so live bots can't run for you here. "
                       f"Paper trading works everywhere.")
    return True, cc


def download(path: Path = DB_PATH) -> Path:
    """Fetch this month's DB-IP country database (falls back to last month's early in a month)."""
    import time
    import urllib.request
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.gmtime()
    months = [f"{now.tm_year}-{now.tm_mon:02d}",
              f"{now.tm_year - (now.tm_mon == 1)}-{(now.tm_mon - 2) % 12 + 1:02d}"]
    for month in months:
        try:
            req = urllib.request.Request(DB_URL.format(month=month), headers={"User-Agent": "poly-martingale-bot"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
        except OSError:
            continue
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return path
    raise OSError("Couldn't download the DB-IP country database")


if __name__ == "__main__":     # python -m poly_bot.geo  ->  download/refresh data/dbip-country-lite.csv.gz
    print("saved", download())
