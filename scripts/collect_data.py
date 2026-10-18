#!/usr/bin/env python3
"""Collect JMA shindo data from the LIVE api/ endpoint into local SQLite.

Strategy (from research.md):
  1) mode=search over rolling time windows (urlencoded POST; verified to
     return the same JSON as multipart).
  2) mode=event for every quake id found (hypocenter + station intensities).

Usage:
  .venv/bin/python scripts/collect_data.py [--db data/shindo.db] \
      [--start 2019-01-01] [--end 2026-10-16] [--step-hours 24] \
      [--sleep 0.3] [--no-details] [--limit N]

Notes:
- Re-run safe: rows are upserted by id, so interrupted runs resume cleanly.
- Politeness: default 0.3s sleep between requests (~3 req/s). Full history
  means tens of thousands of event calls; run it overnight and be kind to
  the server (raise --sleep if in doubt).
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # for app.db helpers

LIVE = "https://www.data.jma.go.jp/eqdb/data/shindo/api/"
DATE_JSON = "https://www.data.jma.go.jp/eqdb/data/shindo/js/date.json"
RETRIES = 3


def post(fields, timeout=60):
    """urlencoded POST with repeated keys; returns parsed JSON."""
    body = urllib.parse.urlencode(fields, doseq=True).encode()
    req = urllib.request.Request(LIVE, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

def default_period():
    with urllib.request.urlopen(DATE_JSON, timeout=30) as resp:
        d = json.loads(resp.read().decode("utf-8"))
    return d.get("st"), d.get("en")


def search_window(dt_from, dt_to, obs_int, max_int, sleep):
    fields = [
        ("mode", "search"),
        ("dateTimeF[]", dt_from.strftime("%Y-%m-%d")),
        ("dateTimeF[]", dt_from.strftime("%H:%M")),
        ("dateTimeT[]", dt_to.strftime("%Y-%m-%d")),
        ("dateTimeT[]", dt_to.strftime("%H:%M")),
        ("pref[]", "99"),
        ("obsInt", obs_int),
        ("maxInt", max_int),
        ("additionalC", "0"),
        ("Sort", "0"),
        ("Comp", "0"),
        ("seisCount", "1"),
        ("observed", "0"),
        ("strParam", ""),
    ]
    for attempt in range(RETRIES):
        try:
            time.sleep(sleep)
            return post(fields)
        except Exception as e:  # noqa: BLE001
            if attempt == RETRIES - 1:
                raise
            print(f"  retry {attempt + 1}/{RETRIES} after error: {e}", flush=True)
            time.sleep(2 ** attempt * 2)


def fetch_event(qid, sleep):
    fields = [("mode", "event"), ("id", qid)]
    for attempt in range(RETRIES):
        try:
            time.sleep(sleep)
            return post(fields)
        except Exception as e:  # noqa: BLE001
            if attempt == RETRIES - 1:
                raise
            time.sleep(2 ** attempt * 2)


def store(conn, qid, event):
    from app.db import upsert_intensity, upsert_quake

    res = event.get("res") or {}
    hyp = res.get("hyp") if isinstance(res, dict) else None
    ints = res.get("int") if isinstance(res, dict) else None
    if not hyp:
        return False
    upsert_quake(conn, hyp[0])
    for r in ints or []:
        upsert_intensity(conn, qid, r)
    conn.commit()
    return True

def resolve_db_path(args) -> str:
    p = Path(args.db)
    return str(p.resolve()) if p.is_absolute() else str((ROOT / p).resolve())


def main() -> int:
    ap = argparse.ArgumentParser(
        description="collect JMA shindo data from the live api/ into SQLite")
    ap.add_argument("--db", default="data/shindo.db")
    ap.add_argument("--start", help="YYYY-MM-DD (default: js/date.json st)")
    ap.add_argument("--end", help="YYYY-MM-DD (default: js/date.json en)")
    ap.add_argument("--step-hours", type=int, default=24)
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--no-details", action="store_true",
                    help="search pass only; skip per-quake mode=event")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N quakes stored (0 = no limit)")
    args = ap.parse_args()

    from app.db import connect, init_db

    db_path = resolve_db_path(args)
    init_db(db_path)
    conn = connect(db_path)

    st, en = default_period()
    start = args.start or st
    end = args.end or en
    step = timedelta(hours=args.step_hours)
    cur = datetime.strptime(start, "%Y-%m-%d")
    stop = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)

    print(f"period {start} .. {end}  step={args.step_hours}h  sleep={args.sleep}s")
    print(f"db: {db_path}", flush=True)

    total_ids: set[str] = set()
    while cur < stop:
        win_to = min(cur + step, stop)
        # Two passes per window (live semantics: maxInt=7 = 震度7 exact):
        #  (obsInt=1, maxInt=6) -> all quakes of 震度1..6
        #  (obsInt=1, maxInt=7) -> 震度7 quakes only
        quakes = []
        try:
            for obs, mx in (("1", "6"), ("1", "7")):
                j = search_window(cur, win_to, obs, mx, args.sleep)
                res = j.get("res")
                if isinstance(res, list):
                    quakes += res
        except Exception as e:  # noqa: BLE001
            print(f"ABORT: window {cur:%Y-%m-%d} failed after retries: {e}")
            return 1
        new = 0
        for q in quakes:
            qid = q.get("id")
            if qid and qid not in total_ids:
                total_ids.add(qid)
                new += 1
        print(f"{cur:%Y-%m-%d}..{win_to:%Y-%m-%d}: {len(quakes)} quakes "
              f"({new} new, total {len(total_ids)})", flush=True)
        cur = win_to

    print(f"search pass done: {len(total_ids)} unique quake ids")
    if args.no_details:
        return 0

    done = 0
    for qid in sorted(total_ids):
        if args.limit and done >= args.limit:
            break
        event = fetch_event(qid, args.sleep)
        if store(conn, qid, event):
            done += 1
            if done % 50 == 0:
                print(f"event details: {done}/{len(total_ids)}", flush=True)
        else:
            print(f"event {qid}: no hyp payload (skipped)", flush=True)
    print(f"event pass done: {done} quakes stored in {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


