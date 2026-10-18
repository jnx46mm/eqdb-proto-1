"""SQLite storage helpers for the local shindo database server.

The schema mirrors the JSON payload shapes returned by the live API
(see research.md) so the local server can reproduce the exact response
contract of https://www.data.jma.go.jp/eqdb/data/shindo/.

Tables:
  earthquakes  -- one row per quake; `id` is yyyyMMddHHmmss of the origin
                  time and doubles as the sort/range key.
  intensities  -- per-station intensity rows for each quake.

Helpers:
  parse_int_level / parse_char_level -- numeric intensity (1..7) parsed
  from the digit classes of the row text (5弱..6強 / 震度7 have NULL
  int_level; the maxInt filter instead ranks maxIcls, see app/api.py).
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS earthquakes (
    id        TEXT PRIMARY KEY,        -- yyyyMMddHHmmss (origin time, seconds)
    ot        TEXT NOT NULL,           -- e.g. '2019/05/20 21:22:48.2'
    name      TEXT NOT NULL,
    latS      TEXT,                    -- DMS string, as-is from live API
    lonS      TEXT,
    lat       TEXT,                    -- verbatim string, e.g. '29.2883'
    lon       TEXT,                    -- verbatim string, e.g. '129.3250'
    lat_num   REAL,                    -- numeric copy for bbox filtering
    lon_num   REAL,
    dep       TEXT,
    mag       TEXT,
    maxI      TEXT,
    maxIcls   TEXT,
    maxS      TEXT,
    maxScls   TEXT,
    int_level REAL                     -- parsed numeric intensity (1..7)
);
CREATE TABLE IF NOT EXISTS intensities (
    quake_id  TEXT NOT NULL,
    code      TEXT NOT NULL,           -- station code, e.g. '7740040'
    name      TEXT,
    lat       TEXT,                    -- verbatim string, e.g. '29.45'
    lon       TEXT,                    -- verbatim string, e.g. '129.605'
    lat_num   REAL,
    lon_num   REAL,
    int       TEXT,                    -- e.g. '震度１'
    char      TEXT,                    -- e.g. '1'
    mark      TEXT,                    -- icon name, e.g. 'S1'
    zidx      TEXT,
    int_level REAL,
    PRIMARY KEY (quake_id, code)
);
CREATE INDEX IF NOT EXISTS idx_quakes_id ON earthquakes(id);
CREATE INDEX IF NOT EXISTS idx_int_quake ON intensities(quake_id);
"""


def get_db_path() -> str:
    """DB file path; APP_DB_PATH env var overrides the repo default."""
    return os.environ.get("APP_DB_PATH", str(_REPO_ROOT / "data" / "shindo.db"))


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str | None = None) -> None:
    """Create the schema (and parent directories) if missing."""
    target = path or get_db_path()
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    with connect(target) as conn:
        conn.executescript(SCHEMA)


_INT_LEVEL_RE = re.compile(r"bg-I(\d+)")


def parse_int_level(maxIcls: str | None) -> float | None:
    """Numeric intensity from a class string like 'bg-I1' ... 'bg-I7'."""
    m = _INT_LEVEL_RE.search(maxIcls or "")
    return float(m.group(1)) if m else None


def parse_char_level(char: str | None) -> float | None:
    """Numeric intensity from the per-station `char` field (e.g. '1')."""
    try:
        return float(char)
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    """Numeric coercion for filtering ('29.2883' -> 29.2883)."""
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


def upsert_quake(conn: sqlite3.Connection, q: dict[str, Any]) -> None:
    """Insert/replace one earthquake row from a live-API style payload."""
    conn.execute(
        "INSERT OR REPLACE INTO earthquakes "
        "(id, ot, name, latS, lonS, lat, lon, lat_num, lon_num, dep, mag, "
        "maxI, maxIcls, maxS, maxScls, int_level) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            q.get("id"),
            q.get("ot"),
            q.get("name"),
            q.get("latS"),
            q.get("lonS"),
            q.get("lat"),
            q.get("lon"),
            _to_float(q.get("lat")),
            _to_float(q.get("lon")),
            q.get("dep"),
            q.get("mag"),
            q.get("maxI"),
            q.get("maxIcls"),
            q.get("maxS"),
            q.get("maxScls"),
            parse_int_level(q.get("maxIcls")),
        ),
    )


def upsert_intensity(conn: sqlite3.Connection, quake_id: str, r: dict[str, Any]) -> None:
    """Insert/replace one per-station intensity row."""
    conn.execute(
        "INSERT OR REPLACE INTO intensities "
        "(quake_id, code, name, lat, lon, lat_num, lon_num, int, char, mark, "
        "zidx, int_level) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            quake_id,
            str(r.get("code")),
            r.get("name"),
            r.get("lat"),
            r.get("lon"),
            _to_float(r.get("lat")),
            _to_float(r.get("lon")),
            r.get("int"),
            r.get("char"),
            r.get("mark"),
            r.get("zidx"),
            parse_char_level(r.get("char")),
        ),
    )
