"""Network tests comparing the local server against the LIVE JMA API.

Skipped by default (no network in CI). Run with:
  RUN_LIVE_TESTS=1 .venv/bin/pytest tests/test_live_contract.py

They verify the structural contract (response shape / key sets) and, for the
seeded sample quake, exact values. If the live DB has more quakes in the same
window than our seeded copy, only the seeded one is compared value-wise.
"""

import os

import httpx
import pytest

RUN_LIVE = os.environ.get("RUN_LIVE_TESTS") == "1"

LIVE_URL = "https://www.data.jma.go.jp/eqdb/data/shindo/api/"
SAMPLE_ID = "20190520212248"  # 2019-05-20 21:22:48 Tokara islands

pytestmark = pytest.mark.skipif(
    not RUN_LIVE, reason="set RUN_LIVE_TESTS=1 to run tests against the live JMA API"
)


def _encode(fields):
    """Pass-through; httpx encodes dict-with-list values as repeated keys."""
    return dict(fields)


def _live(fields):
    r = httpx.post(LIVE_URL, data=_encode(fields), timeout=60.0)
    return r.json()


def test_search_contract_matches_live(client):
    window = {
        "mode": "search",
        "dateTimeF[]": ["2019-05-20", "21:00"],
        "dateTimeT[]": ["2019-05-20", "22:00"],
        "pref[]": ["99"],
        "obsInt": "1",
        "maxInt": "6",
        "additionalC": "0",
        "Sort": "0",
        "Comp": "0",
        "seisCount": "1",
        "observed": "0",
        "strParam": "",
    }
    live = _live(window)
    local = client.post("/api/", data=_encode(window)).json()

    assert isinstance(live["res"], list) and isinstance(local["res"], list)
    if not live["res"]:
        pytest.skip("live window returned no quakes")
    assert set(local["res"][0].keys()) == set(live["res"][0].keys())
    live_ids = {q["id"] for q in live["res"]}
    assert SAMPLE_ID in live_ids
    assert {q["id"] for q in local["res"]} <= live_ids

    sample = next(q for q in live["res"] if q["id"] == SAMPLE_ID)
    local_q = next(q for q in local["res"] if q["id"] == SAMPLE_ID)
    assert local_q == sample


def test_event_contract_matches_live(client):
    fields = {"mode": "event", "id": SAMPLE_ID}
    live = _live(fields)
    local = client.post("/api/", data=_encode(fields)).json()

    assert set(local["res"].keys()) == set(live["res"].keys()) == {"hyp", "int"}
    assert local["res"]["hyp"][0] == live["res"]["hyp"][0]
    if live["res"]["int"]:
        assert set(local["res"]["int"][0].keys()) == set(
            live["res"]["int"][0].keys()
        )
        live_station = {r["code"]: r for r in live["res"]["int"]}
        for row in local["res"]["int"]:
            assert row == live_station[row["code"]]
