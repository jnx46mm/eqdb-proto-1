"""POST handlers that replicate the JMA shindo search API contract.

Live reference (observed via probes, see research.md):
  https://www.data.jma.go.jp/eqdb/data/shindo/api/

- Accepts multipart/form-data (urlencoded also works and is used by tests).
- Always answers JSON: {"res": list|dict|string, "str": list|dict}.

Modes:
  search -- period search over origin times (dateTimeF[]/dateTimeT[]).
  event  -- single quake detail: {"hyp": [...], "int": [...]}.
  int    -- treated like `event` (live server returned an empty body in
            probes; assumption documented in research.md).

Accepted search parameters (array keys "key[]"):
  dateTimeF[]  -- [yyyy-MM-dd, hh:mm] search start
  dateTimeT[]  -- [yyyy-MM-dd, hh:mm] search end
  pref[], city[], station[]  -- pref/station filters ('99' = all)
  obsInt, maxInt             -- intensity range 1..7
  boundsAr[i][]              -- lat/lon corner pairs for bbox filtering
  mag, dep, epi[], additionalC, Sort, Comp, seisCount, observed, strParam
      -- accepted and ignored in this prototype (documented simplification;
         Sort is always newest-first, matching the observed Sort=0 behavior).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .db import connect, get_db_path

router = APIRouter()

ERR_BAD_DATETIME = "地震の発生日時の設定が不正です"
ERR_NOT_FOUND_FMT = "検索に失敗しました（震源）：{qid}"
ERR_UNKNOWN_MODE = "指定されたモードは利用できません"
MSG_NO_RESULT = "検索結果地震数 ： ありませんでした"

# The SPA is served under this path prefix; the frontend fetches "api/"
# relative to /eqdb/data/shindo/index.html.
SPA_DIR = "/eqdb/data/shindo"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_BOUNDS_KEY_RE = re.compile(r"^boundsAr\[(\d+)\]\[\]$")


def _json(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(payload)


def _err(msg: str) -> JSONResponse:
    return _json({"res": msg, "str": [msg]})


def _form_list(form: Any, key: str) -> list[str]:
    """Values of 'key[]' (array syntax used by the SPA) or bare 'key'."""
    raw = form.getlist(key + "[]") or form.getlist(key)
    return [str(v) for v in raw if str(v) != ""]


def _parse_dt_pair(date_s: str, time_s: str) -> datetime | None:
    if not _DATE_RE.match(date_s) or not _TIME_RE.match(time_s):
        return None
    try:
        h, m = (int(x) for x in time_s.split(":"))
        return datetime.strptime(date_s, "%Y-%m-%d").replace(hour=h, minute=m)
    except ValueError:
        return None


def _int_or_none(v: Any) -> int | None:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def _parse_bounds(form: Any) -> list[tuple[float, float]] | None:
    """Collect boundsAr[i][] corner pairs; None if any value is malformed."""
    groups: dict[int, list[str]] = {}
    for key in form.keys():
        m = _BOUNDS_KEY_RE.match(key)
        if m:
            groups.setdefault(int(m.group(1)), []).extend(form.getlist(key))
    pts: list[tuple[float, float]] = []
    for i in sorted(groups):
        vals = [v for v in groups[i] if str(v) != ""]
        if len(vals) < 2:
            continue
        try:
            pts.append((float(vals[0]), float(vals[1])))
        except (TypeError, ValueError):
            return None
    return pts


def _split_combined(vals: list[str]) -> list[str]:
    """Tolerate a combined single value 'yyyy-MM-dd hh:mm'."""
    if len(vals) == 1 and " " in vals[0]:
        return vals[0].split(" ", 1)
    return vals

def _quake_dict(r: Any) -> dict[str, Any]:
    """Key order matches the live API payload."""
    return {
        "id": r["id"],
        "ot": r["ot"],
        "name": r["name"],
        "latS": r["latS"],
        "lonS": r["lonS"],
        "lat": r["lat"],
        "lon": r["lon"],
        "dep": r["dep"],
        "mag": r["mag"],
        "maxI": r["maxI"],
        "maxIcls": r["maxIcls"],
        "maxS": r["maxS"],
        "maxScls": r["maxScls"],
    }


def _int_dict(r: Any) -> dict[str, Any]:
    """Key order matches the live API payload."""
    return {
        "name": r["name"],
        "lat": r["lat"],
        "lon": r["lon"],
        "code": r["code"],
        "int": r["int"],
        "char": r["char"],
        "mark": r["mark"],
        "zidx": r["zidx"],
    }


def _handle_search(form: Any) -> JSONResponse:
    f = _split_combined(_form_list(form, "dateTimeF"))
    t = _split_combined(_form_list(form, "dateTimeT"))
    if len(f) < 2 or len(t) < 2:
        return _err(ERR_BAD_DATETIME)
    dt_from = _parse_dt_pair(f[0], f[1])
    dt_to = _parse_dt_pair(t[0], t[1])
    if dt_from is None or dt_to is None:
        return _err(ERR_BAD_DATETIME)

    # `id` is a fixed-length yyyyMMddHHmmss key, so lexicographic compare
    # equals chronological compare.
    key_from = dt_from.strftime("%Y%m%d%H%M") + "00"
    key_to = dt_to.strftime("%Y%m%d%H%M") + "59"
    if key_from > key_to:
        return _err(ERR_BAD_DATETIME)

    date_line = (
        f"地震の発生日時 ： {dt_from:%Y/%m/%d}　{f[1]}　～　"
        f"{dt_to:%Y/%m/%d}　{t[1]}"
    )

    conds = ["id >= ?", "id <= ?"]
    params: list[Any] = [key_from, key_to]

    obs_int = _int_or_none(form.get("obsInt"))
    max_int = _int_or_none(form.get("maxInt"))
    max_line: list[str] = []
    if max_int is not None and max_int >= 7:
        # Live semantics (verified 2026-10-18): upper bound 震度7 acts as
        # "震度7 (exact)" — obsInt is effectively ignored — and an extra
        # str line "最大震度 ： 震度７" is added (even for 0 results).
        max_line = ["最大震度 ： 震度７"]
        conds.append("int_level = 7")
    else:
        if obs_int is not None:
            conds.append("int_level >= ?")
            params.append(float(obs_int))
        if max_int is not None:
            conds.append("int_level <= ?")
            params.append(float(max_int))

    stations = _form_list(form, "station")
    if stations and "99" not in stations:
        marks = ",".join("?" for _ in stations)
        conds.append(
            f"EXISTS (SELECT 1 FROM intensities i WHERE i.quake_id = e.id "
            f"AND i.code IN ({marks}))"
        )
        params.extend(stations)

    bounds = _parse_bounds(form)
    if bounds:
        lats = [p[0] for p in bounds]
        lons = [p[1] for p in bounds]
        conds.append("lat_num BETWEEN ? AND ? AND lon_num BETWEEN ? AND ?")
        params.extend([min(lats), max(lats), min(lons), max(lons)])

    conn = connect(get_db_path())
    try:
        rows = conn.execute(
            "SELECT * FROM earthquakes e WHERE " + " AND ".join(conds)
            + " ORDER BY id DESC",
            params,
        ).fetchall()
    finally:
        conn.close()

    quakes = [_quake_dict(r) for r in rows]
    n = len(quakes)
    if n == 0:
        return _json({"res": MSG_NO_RESULT,
                      "str": [date_line] + max_line + [MSG_NO_RESULT]})
    count_line = f"検索結果地震数 ： {n} 地震 （「地震の発生日時の新しい順」で検索）"
    return _json({"res": quakes, "str": [date_line] + max_line + [count_line]})


def _handle_event(form: Any) -> JSONResponse:
    qid = str(form.get("id") or "").strip()
    if not re.fullmatch(r"\d{14}", qid):
        return _err(ERR_BAD_DATETIME)

    conn = connect(get_db_path())
    try:
        row = conn.execute(
            "SELECT * FROM earthquakes WHERE id = ?", (qid,)
        ).fetchone()
        int_rows = conn.execute(
            "SELECT * FROM intensities WHERE quake_id = ? ORDER BY code", (qid,)
        ).fetchall()
    finally:
        conn.close()

    if row is None:
        msg = ERR_NOT_FOUND_FMT.format(qid=qid)
        return _json({"res": msg, "str": [msg]})

    payload = {"hyp": [_quake_dict(row)], "int": [_int_dict(r) for r in int_rows]}
    return _json({"res": payload, "str": payload})


@router.post(SPA_DIR + "/api/")
@router.post("/api/")
async def api(request: Request) -> JSONResponse:
    form = await request.form()
    mode = str(form.get("mode") or "").strip()
    if mode == "search":
        return _handle_search(form)
    if mode in ("event", "int"):
        return _handle_event(form)
    return _err(ERR_UNKNOWN_MODE)

