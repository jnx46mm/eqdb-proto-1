"""Contract tests for the local /api/ endpoint (no network needed)."""

import pytest


def _encode(fields):
    """Pass-through; TestClient/httpx encode dict-with-list values as
    repeated urlencoded keys (mimicking the SPA's form arrays)."""
    return dict(fields)


def _search_fields(f_date="2019-05-20", f_time="21:00",
                   t_date="2019-05-20", t_time="22:00"):
    return {
        "mode": "search",
        "dateTimeF[]": [f_date, f_time],
        "dateTimeT[]": [t_date, t_time],
        "pref[]": ["99"],
        "city[]": [],
        "station[]": [],
        "obsInt": "1",
        "maxInt": "6",
        "additionalC": "0",
        "Sort": "0",
        "Comp": "0",
        "seisCount": "1",
        "observed": "0",
        "strParam": "",
    }


def _post(client, fields, path="/api/"):
    return client.post(path, data=_encode(fields))


def test_search_found(client):
    r = _post(client, _search_fields())
    assert r.status_code == 200
    j = r.json()
    assert isinstance(j["res"], list) and len(j["res"]) == 1
    q = j["res"][0]
    assert q["id"] == "20190520212248"
    assert q["ot"] == "2019/05/20 21:22:48.2"
    assert q["name"] == "トカラ列島近海"
    assert q["latS"] == "29°17.3′N"
    assert q["maxIcls"] == "bg-I1"
    assert isinstance(j["str"], list)
    assert j["str"][0] == "地震の発生日時 ： 2019/05/20\u300021:00\u3000～\u30002019/05/20\u300022:00"
    assert j["str"][1] == "検索結果地震数 ： 1 地震 （「地震の発生日時の新しい順」で検索）"


def test_search_empty_window(client):
    j = _post(client, _search_fields("2100-01-01", "00:00",
                                     "2100-01-02", "00:00")).json()
    assert j["res"] == "検索結果地震数 ： ありませんでした"
    assert isinstance(j["str"], list) and len(j["str"]) == 2


def test_search_invalid_datetime_missing_to(client):
    fields = _search_fields()
    del fields["dateTimeT[]"]
    j = _post(client, fields).json()
    assert j["res"] == "地震の発生日時の設定が不正です"
    assert isinstance(j["str"], list)


def test_search_reversed_range_is_error(client):
    j = _post(client, _search_fields("2019-05-20", "22:00",
                                     "2019-05-20", "21:00")).json()
    assert j["res"] == "地震の発生日時の設定が不正です"


def test_search_station_filter(client):
    hit = _search_fields()
    hit["station[]"] = ["7740040"]
    assert isinstance(_post(client, hit).json()["res"], list)

    miss = _search_fields()
    miss["station[]"] = ["1000000"]
    assert _post(client, miss).json()["res"] == \
        "検索結果地震数 ： ありませんでした"

    wildcard = _search_fields()
    wildcard["station[]"] = ["99"]
    assert isinstance(_post(client, wildcard).json()["res"], list)


def test_search_intensity_range_filter(client):
    out_of_range = _search_fields()
    out_of_range["obsInt"] = "2"  # seed quake has 震度１ only
    assert _post(client, out_of_range).json()["res"] == \
        "検索結果地震数 ： ありませんでした"

    in_range = _search_fields()
    in_range["maxInt"] = "1"
    assert isinstance(_post(client, in_range).json()["res"], list)


def test_search_bounds_filter(client):
    inside = _search_fields()
    inside["boundsAr[0][]"] = ["29.0", "129.0"]
    inside["boundsAr[1][]"] = ["29.5", "129.7"]
    assert isinstance(_post(client, inside).json()["res"], list)

    outside = _search_fields()
    outside["boundsAr[0][]"] = ["35.0", "139.0"]
    outside["boundsAr[1][]"] = ["36.0", "140.0"]
    assert _post(client, outside).json()["res"] == \
        "検索結果地震数 ： ありませんでした"


def test_search_maxint7_semantics(client):
    """Live semantics: maxInt=7 = 震度7 exact; obsInt ignored; extra str line."""
    fields = _search_fields("1995-01-17", "00:00", "1995-01-17", "23:59")
    fields["maxInt"] = "7"
    j = _post(client, fields).json()
    assert [q["id"] for q in j["res"]] == ["19950117054651"]
    assert j["str"] == [
        "地震の発生日時 ： 1995/01/17\u300000:00\u3000～\u30001995/01/17\u300023:59",
        "最大震度 ： 震度７",
        "検索結果地震数 ： 1 地震 （「地震の発生日時の新しい順」で検索）",
    ]

    # obsInt is effectively ignored when maxInt=7 (verified against live).
    fields["obsInt"] = "5"
    assert isinstance(_post(client, fields).json()["res"], list)

    # Window without any 震度7 quake: empty result, but the max line stays.
    fields2 = _search_fields()
    fields2["maxInt"] = "7"
    j2 = _post(client, fields2).json()
    assert j2["res"] == "検索結果地震数 ： ありませんでした"
    assert j2["str"][1] == "最大震度 ： 震度７"
    assert len(j2["str"]) == 3


def test_event_found(client):
    j = _post(client, {"mode": "event", "id": "20190520212248"}).json()
    hyp = j["res"]["hyp"]
    assert len(hyp) == 1 and hyp[0]["id"] == "20190520212248"
    assert hyp[0]["ot"] == "2019/05/20 21:22:48.2"
    ints = j["res"]["int"]
    assert len(ints) == 1 and ints[0]["code"] == "7740040"
    assert ints[0]["int"] == "震度１" and ints[0]["mark"] == "S1"
    assert set(j["res"].keys()) == {"hyp", "int"}


def test_event_missing_id(client):
    j = _post(client, {"mode": "event", "id": "19990101000000"}).json()
    assert j["res"].startswith("検索に失敗しました（震源）")


def test_event_bad_id_is_error(client):
    j = _post(client, {"mode": "event", "id": "nope"}).json()
    assert j["res"] == "地震の発生日時の設定が不正です"


def test_spa_path_alias(client):
    """The SPA resolves fetch('api/') to /eqdb/data/shindo/api/."""
    r = _post(client, _search_fields(), path="/eqdb/data/shindo/api/")
    assert r.status_code == 200
    assert isinstance(r.json()["res"], list)


def test_unknown_mode(client):
    j = _post(client, {"mode": "bogus"}).json()
    assert j["res"] == "指定されたモードは利用できません"
    assert "str" in j


def test_response_contract_keys(client):
    """Every response has exactly the {res, str} envelope."""
    for fields in (_search_fields(), {"mode": "event",
                                      "id": "20190520212248"},
                   {"mode": "bogus"}):
        keys = set(_post(client, fields).json().keys())
        assert keys == {"res", "str"}
