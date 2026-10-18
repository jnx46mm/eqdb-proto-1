"""POST handlers that replicate the JMA shindo search API contract.

Live reference (observed via probes, see research.md):
  https://www.data.jma.go.jp/eqdb/data/shindo/api/

- Accepts multipart/form-data (urlencoded also works and is used by tests).
- Successful calls answer JSON: {"res": list|dict|string, "str": list|dict}.
- Unknown/missing mode: the live server answers an EMPTY 200 body
  (content-type text/html; charset=UTF-8) -- replicated here.

Modes:
  search -- period search over origin times (dateTimeF[]/dateTimeT[]).
  event  -- single quake detail: {"hyp": [...], "int": [...]}.

Verified search contract (probes of 2026-10-18, see research.md §8):

  dateTimeF[]/dateTimeT[]  [yyyy-MM-dd, hh:mm] -- required, first two array
    items used (extras ignored).  Validation pipeline:
      (a) item-count or format failures -> 「地震の発生日時の設定が不正です」
          (short-circuits all other checks);
      (b) value failures are aggregated, in the order 開始日/終了日/
          開始時刻/終了時刻, into 「検索開始日」「検索終了時刻」...が不正です;
      (c) start > end -> 「地震の発生日時の設定が逆転しています」.
    1-digit hours ("5:00") are accepted; hour >= 24 or minute >= 60 is a
    value failure.

  maxInt -- the ONLY honored intensity filter.  Valid codes:
      2/3/4 -> 震度N以上, A -> 震度５弱以上, B -> 震度５強以上,
      C -> 震度６弱以上, D -> 震度６強以上 (all ">= class"), and
      7 -> 震度７ exact.  Any other value (incl. 1/5/6/9) is ignored.
      A valid code adds the str line 「最大震度 ： ...」 even for 0 results.

  Sort -- S0 発生日時の新しい順 (default) / S1 発生日時の古い順 /
      S2 最大震度の大きい順 / S3 地震の規模の大きい順.  Ties break
      newest-first; S3 sorts non-numeric mag (不明) last.  The count line
      in str mirrors the sort wording; invalid codes fall back to S0.

  boundsAr[i][] -- lat/lon corner pairs; bbox filter (verified live).

  obsInt, pref[], city[], station[], epi[], mag[]/mag, dep[]/dep,
  additionalC, observed, seisCount, Comp, strParam
      -- accepted and IGNORED by the live server (verified); this prototype
         ignores them too.  (Scalar mag/dep/epi values crash the live
         server with a 500 page; the array form is simply ignored.)

Event mode honors only `id` (Q[] -- the SPA's station/city/pref filter --
is ignored by the live server):
  - no id at all -> 「検索に失敗しました」
  - id not found -> 「検索に失敗しました（震源）：{id}」 (live does the same
    for short numeric ids such as "2011"; for non-numeric ids it crashes
    with a 500 page -- this prototype replies with the not-found message
    instead, a documented divergence).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .db import connect, get_db_path

router = APIRouter()

ERR_BAD_DATETIME = "地震の発生日時の設定が不正です"
ERR_REVERSED_DATETIME = "地震の発生日時の設定が逆転しています"
ERR_NOT_FOUND = "検索に失敗しました"
ERR_NOT_FOUND_FMT = "検索に失敗しました（震源）：{qid}"
MSG_NO_RESULT = "検索結果地震数 ： ありませんでした"

# The SPA is served under this path prefix; the frontend fetches "api/"
# relative to /eqdb/data/shindo/index.html.
SPA_DIR = "/eqdb/data/shindo"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_BOUNDS_KEY_RE = re.compile(r"^boundsAr\[(\d+)\]\[\]$")

# maxInt code -> (rank threshold, str line label).  Ranks place the JMA
# intensity classes on one ordinal scale (bg-IA..bg-ID = ５弱..６強).
MAX_INT_CODES: dict[str, tuple[int, str]] = {
    "2": (2, "震度２以上"),
    "3": (3, "震度３以上"),
    "4": (4, "震度４以上"),
    "A": (5, "震度５弱以上"),
    "B": (6, "震度５強以上"),
    "C": (7, "震度６弱以上"),
    "D": (8, "震度６強以上"),
    "7": (9, "震度７"),  # 震度7 exact (rank 9 exists only for bg-I7)
}

# Ordinal rank of a quake's maxIcls class (1..9; unknown class -> 1).
_INT_RANK_SQL = (
    "CASE maxIcls"
    " WHEN 'bg-I7' THEN 9 WHEN 'bg-ID' THEN 8 WHEN 'bg-IC' THEN 7"
    " WHEN 'bg-IB' THEN 6 WHEN 'bg-IA' THEN 5 WHEN 'bg-I4' THEN 4"
    " WHEN 'bg-I3' THEN 3 WHEN 'bg-I2' THEN 2 ELSE 1 END"
)

# Sort code -> (count-line wording, ORDER BY clause).  Ties break
# newest-first (id DESC); S3 puts non-numeric mag (不明) last.
SORT_CODES: dict[str, tuple[str, str]] = {
    "S0": ("地震の発生日時の新しい順", "id DESC"),
    "S1": ("地震の発生日時の古い順", "id ASC"),
    "S2": ("最大震度の大きい順", _INT_RANK_SQL + " DESC, id DESC"),
    "S3": ("地震の規模の大きい順",
           "CASE WHEN mag GLOB '[0-9]*' THEN CAST(mag AS REAL) ELSE -1 END"
           " DESC, id DESC"),
}


def _json(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(payload)


def _err(msg: str) -> JSONResponse:
    return _json({"res": msg, "str": [msg]})


def _form_list(form: Any, key: str) -> list[str]:
    """Values of 'key[]' (array syntax used by the SPA) or bare 'key'."""
    raw = form.getlist(key + "[]") or form.getlist(key)
    return [str(v) for v in raw if str(v) != ""]


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


def _valid_date(s: str) -> bool:
    """A format-valid date string must also name a real calendar day."""
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _valid_time(s: str) -> bool:
    """Format-valid times must have hour < 24 and minute < 60."""
    h, m = s.split(":")
    return int(h) < 24 and int(m) < 60

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
    f_date, f_time = f[0], f[1]
    t_date, t_time = t[0], t[1]

    # (a) format check short-circuits everything else (verified live).
    if not (_DATE_RE.match(f_date) and _DATE_RE.match(t_date)
            and _TIME_RE.match(f_time) and _TIME_RE.match(t_time)):
        return _err(ERR_BAD_DATETIME)

    # (b) value failures aggregate in the order 開始日/終了日/開始時刻/終了時刻.
    bad: list[str] = []
    if not _valid_date(f_date):
        bad.append("「検索開始日」")
    if not _valid_date(t_date):
        bad.append("「検索終了日」")
    if not _valid_time(f_time):
        bad.append("「検索開始時刻」")
    if not _valid_time(t_time):
        bad.append("「検索終了時刻」")
    if bad:
        return _err("".join(bad) + "が不正です")

    fh, fm = (int(x) for x in f_time.split(":"))
    th, tm = (int(x) for x in t_time.split(":"))
    dt_from = datetime.strptime(f_date, "%Y-%m-%d").replace(hour=fh, minute=fm)
    dt_to = datetime.strptime(t_date, "%Y-%m-%d").replace(hour=th, minute=tm)

    # (c) reversal is only checked once all values are valid.
    if dt_from > dt_to:
        return _err(ERR_REVERSED_DATETIME)

    # `id` is a fixed-length yyyyMMddHHmmss key, so lexicographic compare
    # equals chronological compare.
    key_from = dt_from.strftime("%Y%m%d%H%M") + "00"
    key_to = dt_to.strftime("%Y%m%d%H%M") + "59"
    if key_from > key_to:
        return _err(ERR_REVERSED_DATETIME)

    date_line = (
        f"地震の発生日時 ： {dt_from:%Y/%m/%d}　{f_time}　～　"
        f"{dt_to:%Y/%m/%d}　{t_time}"
    )

    conds = ["id >= ?", "id <= ?"]
    params: list[Any] = [key_from, key_to]

    # maxInt is the only honored intensity filter (obsInt is ignored).
    code = str(form.get("maxInt") or "").strip()
    max_line: list[str] = []
    if code in MAX_INT_CODES:
        threshold, label = MAX_INT_CODES[code]
        max_line = [f"最大震度 ： {label}"]
        conds.append(f"{_INT_RANK_SQL} >= ?")
        params.append(float(threshold))

    bounds = _parse_bounds(form)
    if bounds:
        lats = [p[0] for p in bounds]
        lons = [p[1] for p in bounds]
        conds.append("lat_num BETWEEN ? AND ? AND lon_num BETWEEN ? AND ?")
        params.extend([min(lats), max(lats), min(lons), max(lons)])

    sort = (_form_list(form, "Sort") or [""])[0]
    wording, order_by = SORT_CODES.get(sort, SORT_CODES["S0"])

    conn = connect(get_db_path())
    try:
        rows = conn.execute(
            "SELECT * FROM earthquakes e WHERE " + " AND ".join(conds)
            + f" ORDER BY {order_by}",
            params,
        ).fetchall()
    finally:
        conn.close()

    quakes = [_quake_dict(r) for r in rows]
    n = len(quakes)
    if n == 0:
        return _json({"res": MSG_NO_RESULT,
                      "str": [date_line] + max_line + [MSG_NO_RESULT]})
    count_line = f"検索結果地震数 ： {n} 地震 （「{wording}」で検索）"
    return _json({"res": quakes, "str": [date_line] + max_line + [count_line]})


def _handle_event(form: Any) -> JSONResponse:
    qid = str(form.get("id") or "").strip()
    if not qid:
        # Live: no id at all -> plain 検索に失敗しました (verified 2026-10-18).
        return _err(ERR_NOT_FOUND)

    # Note: the live server validates no id format here -- short numeric ids
    # such as "2011" get the not-found message and non-numeric ids crash the
    # server with a 500 page.  This prototype replies with the not-found
    # message in both cases (documented divergence, see research.md).
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
    if mode == "event":
        return _handle_event(form)
    # Live: mode=int and unknown/missing modes answer an EMPTY 200 body
    # (content-type text/html; charset=UTF-8) -- verified 2026-10-18.
    return Response(status_code=200, media_type="text/html; charset=UTF-8")

