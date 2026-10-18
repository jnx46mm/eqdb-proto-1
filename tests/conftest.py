"""Shared test fixtures: a temp SQLite DB seeded with one real quake.

Seed record mirrors the live API response for quake id 20190520212248
(Tokara islands, 2019-05-20 21:22:48, station 7740040 悪石島, 震度１),
observed during the research phase.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_DB_TMP = Path(tempfile.gettempdir()) / "eqdb_proto_test.sqlite3"
os.environ["APP_DB_PATH"] = str(_DB_TMP)
# Do not mount the real ./static while testing.
os.environ["APP_STATIC_DIR"] = str(ROOT / "tests" / "no_such_static_dir")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SEED_QUAKE = {
    "id": "20190520212248",
    "ot": "2019/05/20 21:22:48.2",
    "name": "トカラ列島近海",
    "latS": "29°17.3′N",
    "lonS": "129°19.5′E",
    "lat": "29.2883",
    "lon": "129.3250",
    "dep": "15 km",
    "mag": "2.6",
    "maxI": "震度１",
    "maxIcls": "bg-I1",
    "maxS": None,
    "maxScls": None,
}

SEED_INT = {
    "code": "7740040",
    "name": "鹿児島十島村悪石島＊",
    "lat": "29.45",
    "lon": "129.605",
    "int": "震度１",
    "char": "1",
    "mark": "S1",
    "zidx": "1",
}

# Second seed: the 1995 Kobe quake (震度７) — used for maxInt=7 semantics
# tests (hypocenter values as returned by the live API; no intensity rows
# are seeded for it).
SEED_QUAKE_KOBE = {
    "id": "19950117054651",
    "ot": "1995/01/17 05:46:51.8",
    "name": "大阪湾",
    "latS": "34°35.9′N",
    "lonS": "135°02.1′E",
    "lat": "34.5983",
    "lon": "135.0350",
    "dep": "16 km",
    "mag": "7.3",
    "maxI": "震度７",
    "maxIcls": "bg-I7",
    "maxS": None,
    "maxScls": None,
}


@pytest.fixture(scope="session")
def client():
    from app.db import connect, init_db, upsert_intensity, upsert_quake
    from app.main import app

    if _DB_TMP.exists():
        _DB_TMP.unlink()
    init_db(str(_DB_TMP))
    conn = connect(str(_DB_TMP))
    upsert_quake(conn, SEED_QUAKE)
    upsert_intensity(conn, SEED_QUAKE["id"], SEED_INT)
    upsert_quake(conn, SEED_QUAKE_KOBE)
    conn.commit()
    conn.close()
    return TestClient(app)
