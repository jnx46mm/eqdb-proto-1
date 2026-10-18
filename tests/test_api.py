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
    """Live: reversal error message differs from the bad-format message."""
    j = _post(client, _search_fields("2019-05-20", "22:00",
                                     "2019-05-20", "21:00")).json()
    assert j["res"] == "地震の発生日時の設定が逆転しています"


def test_search_datetime_value_errors(client):
    """Live: value failures aggregate per field, format failures
    short-circuit (see round 6/7 probes)."""
    # 開始時刻 out of range
    j = _post(client, _search_fields("2019-05-20", "25:00")).json()
    assert j["res"] == "「検索開始時刻」が不正です"
    # 終了時刻 out of range
    j = _post(client, _search_fields("2019-05-20", "21:00",
                                     "2019-05-20", "25:00")).json()
    assert j["res"] == "「検索終了時刻」が不正です"
    # 開始日 / 終了日 out of range (no Feb 30)
    j = _post(client, _search_fields("2011-02-30")).json()
    assert j["res"] == "「検索開始日」が不正です"
    j = _post(client, _search_fields("2019-05-20", "21:00",
                                     "2011-02-30", "22:00")).json()
    assert j["res"] == "「検索終了日」が不正です"
    # Aggregation in the order 開始日/終了日/開始時刻/終了時刻
    j = _post(client, _search_fields("2011-02-30", "21:00",
                                     "2019-05-20", "25:00")).json()
    assert j["res"] == "「検索開始日」「検索終了時刻」が不正です"
    # Format failures short-circuit even if other values are also invalid.
    j = _post(client, _search_fields("xx-02-30", "21:00",
                                     "2019-05-20", "25:00")).json()
    assert j["res"] == "地震の発生日時の設定が不正です"
    # 1-digit hours are accepted by the live server.
    assert isinstance(_post(client, _search_fields("2019-05-20", "5:00",
                                                   "2019-05-20", "22:00"))
                      .json()["res"], list)


def test_search_ignores_dead_params(client):
    """obsInt, pref/city/station, mag/dep/epi arrays etc. are accepted and
    IGNORED by the live server (verified in probes)."""
    for key, val in (("obsInt", "2"), ("obsInt", "7"),
                     ("pref[]", ["34"]), ("city[]", ["34203"]),
                     ("mag[]", ["5.5"]), ("dep[]", ["50"]),
                     ("epi[]", ["34203"]), ("additionalC", "1"),
                     ("seisCount", "1"), ("observed", "1"),
                     ("Comp", "C1"), ("strParam", "x")):
        fields = _search_fields()
        fields[key] = val
        fields.setdefault("station[]", ["1000000"])  # would not match seed
        assert isinstance(_post(client, fields).json()["res"], list), key


def test_search_maxint_codes(client):
    """Live: maxInt uses letter codes >=-class semantics; invalid codes
    (incl. 1/5/6/9) are silently ignored."""
    # Seed window quake has 震度１ only: any valid threshold > 1 empties it,
    # while the max line is still added.
    j = _post(client, _search_fields_with_maxint("2")).json()
    assert j["res"] == "検索結果地震数 ： ありませんでした"
    assert j["str"][1] == "最大震度 ： 震度２以上"
    assert len(j["str"]) == 3

    # Kobe window (震度7): passes every lower threshold.
    for code, label in (("4", "震度４以上"), ("A", "震度５弱以上"),
                        ("B", "震度５強以上"), ("C", "震度６弱以上"),
                        ("D", "震度６強以上"), ("7", "震度７")):
        fields = _search_fields("1995-01-17", "00:00", "1995-01-17", "23:59")
        fields["maxInt"] = code
        j = _post(client, fields).json()
        assert [q["id"] for q in j["res"]] == ["19950117054651"], code
        assert j["str"][1] == f"最大震度 ： {label}", code

    # Invalid codes (incl. digits that are not class ids) are ignored.
    for bad in ("1", "5", "6", "9", "7x", "abc"):
        fields = _search_fields()
        fields["maxInt"] = bad
        j = _post(client, fields).json()
        assert isinstance(j["res"], list), bad
        assert len(j["str"]) == 2, bad  # no 最大震度 line


def _search_fields_with_maxint(code, **kwargs):
    fields = _search_fields(**kwargs)
    fields["maxInt"] = code
    return fields


def test_search_sort_codes(client):
    """Live: Sort S0-S3 reorder results and change the count-line wording;
    bare digits fall back to S0.  A window covering both seeds keeps this
    deterministic (Tokara 2019 mag 2.6 震度1; Kobe 1995 mag 7.3 震度7)."""
    window = ("1995-01-17", "00:00", "2019-05-20", "23:59")

    def ids_and_line(code):
        fields = _search_fields(*window)
        fields["Sort"] = code
        j = _post(client, fields).json()
        return ([q["id"] for q in j["res"]], j["str"][1])

    ids, line = ids_and_line("S0")
    assert ids == ["20190520212248", "19950117054651"]
    assert line == "検索結果地震数 ： 2 地震 （「地震の発生日時の新しい順」で検索）"

    ids, line = ids_and_line("S1")
    assert ids == ["19950117054651", "20190520212248"]
    assert line == "検索結果地震数 ： 2 地震 （「地震の発生日時の古い順」で検索）"

    ids, line = ids_and_line("S2")
    assert ids == ["19950117054651", "20190520212248"]  # 震度7 first
    assert line == "検索結果地震数 ： 2 地震 （「最大震度の大きい順」で検索）"

    ids, line = ids_and_line("S3")
    assert ids == ["19950117054651", "20190520212248"]  # mag 7.3 first
    assert line == "検索結果地震数 ： 2 地震 （「地震の規模の大きい順」で検索）"

    # Bare digits are ignored by the live server -> S0 wording.
    ids, line = ids_and_line("0")
    assert ids == ["20190520212248", "19950117054651"]
    assert line == "検索結果地震数 ： 2 地震 （「地震の発生日時の新しい順」で検索）"

    # Invalid sort codes fall back to S0 wording, newest first.
    ids, line = ids_and_line("S9")
    assert ids == ["20190520212248", "19950117054651"]
    assert line == "検索結果地震数 ： 2 地震 （「地震の発生日時の新しい順」で検索）"


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
    assert j["res"] == "検索に失敗しました（震源）：19990101000000"
    assert j["str"] == [j["res"]]


def test_event_no_id_at_all(client):
    """Live: no id field -> plain 検索に失敗しました."""
    j = _post(client, {"mode": "event"}).json()
    assert j["res"] == "検索に失敗しました"
    assert j["str"] == [j["res"]]


def test_event_bad_id_is_not_found(client):
    """Live: short numeric ids get the not-found message; non-numeric ids
    500 on live -- here both reply with the not-found message."""
    for qid in ("2011", "nope"):
        j = _post(client, {"mode": "event", "id": qid}).json()
        assert j["res"] == f"検索に失敗しました（震源）：{qid}"


def test_event_q_array_is_ignored(client):
    """Q[] (the SPA's station filter in event mode) is ignored: the full
    intensity list is returned (verified live -- always 2901 rows there)."""
    fields = {"mode": "event", "id": "20190520212248", "Q[]": ["1000000"]}
    j = _post(client, fields).json()
    assert len(j["res"]["int"]) == 1
    assert j["res"]["int"][0]["code"] == "7740040"


def test_spa_path_alias(client):
    """The SPA resolves fetch('api/') to /eqdb/data/shindo/api/."""
    r = _post(client, _search_fields(), path="/eqdb/data/shindo/api/")
    assert r.status_code == 200
    assert isinstance(r.json()["res"], list)


def test_unknown_mode_is_empty_body(client):
    """Live: mode=int / unknown / missing modes answer an EMPTY 200 body
    with content-type text/html; charset=UTF-8."""
    for fields in ({"mode": "int"}, {"mode": "bogus"}, {}):
        r = _post(client, fields)
        assert r.status_code == 200
        assert r.text == ""
        assert r.headers["content-type"] == "text/html; charset=UTF-8"


def test_response_contract_keys(client):
    """Every successful mode answers with exactly the {res, str} envelope."""
    for fields in (_search_fields(),
                   {"mode": "event", "id": "20190520212248"}):
        keys = set(_post(client, fields).json().keys())
        assert keys == {"res", "str"}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root_redirect(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/eqdb/data/shindo/index.html"
