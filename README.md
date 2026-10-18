# eqdb-proto-1 — ローカル再現プロトタイプ(JMA 震度データベース)

JMA「震度データベース」サイト
(https://www.data.jma.go.jp/eqdb/data/shindo/index.html) をローカルで再現する
プロトタイプ。実装方針・API契約の全容は [research.md](research.md) を参照。

## 構成

```
app/
  main.py        FastAPI エントリ(静的ファイルマウント + /health)
  api.py         POST /api/ のコントラクト再現(search/event/int)
  db.py          SQLite スキーマと CRUD ヘルパ
scripts/
  collect_data.py    本番 API から data/shindo.db へ収集(再実行安全・upsert)
  mirror_static.py   フロント資産を static/ へミラー
tests/
  conftest.py        実データ(20190520212248)をシードしたテスト用 DB
  test_api.py        ローカル API コントラクト検証(ネットワーク不要)
  test_live_contract.py  本番 API との突き合わせ検証(RUN_LIVE_TESTS=1 時のみ)
```

## セットアップ

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## データ収集(本番 API から)

```sh
# 全期間(js/date.json の st..en)。件数は数万〜。 politeness デフォルト 0.3s
.venv/bin/python scripts/collect_data.py
# 短く試す例: 2019-05-20 の 1 日分(詳細取得あり)
.venv/bin/python scripts/collect_data.py --start 2019-05-20 --end 2019-05-20
# 検索パスのみ(高速に件数把握)
.venv/bin/python scripts/collect_data.py --no-details
```

収集は時間窓ごとに **1 パス**で検索する: `(obsInt=1, maxInt=6)`。本番の
`maxInt` は `2/3/4/A/B/C/D` (震度N以上) と `7` (震度7のみ) だけが有効で、
`6` などの無効な値は黙って無視されるため、この 1 パスで全件が取れる
(research.md §8)。

## フロント資産のミラー

```sh
.venv/bin/python scripts/mirror_static.py   # static/eqdb/data/shindo/... に保存
```

## サーバ起動

```sh
.venv/bin/uvicorn app.main:app --port 8000
# ブラウザ: http://localhost:8000/eqdb/data/shindo/index.html
```

- SPA は相対 `fetch("api/")` を `/eqdb/data/shindo/api/` に解決するため、
  同一パスの POST ルートを用意してある(エイリアス `/api/` もあり)。
- 資産内の絶対参照 `eqdb/data/shindo/images/...` に合わせ、`static/` を
  ルートマウントしている。

## API契約(再現したもの)

- `POST /eqdb/data/shindo/api/`(および `/api/`), multipart/urlencoded 可。
- レスポンスは常に `{"res": ..., "str": ...}` JSON。
  - `mode=search`:
    - ヒット時 `res` は地震配列(キー順も本番どおり: id,ot,name,latS,lonS,
      lat,lon,dep,mag,maxI,maxIcls,maxS,maxScls。lat/lon は本番同様の
      文字列)。`str` は [発生日時行, (最大震度行), 件数行]。
    - 0件時 `res` = "検索結果地震数 ： ありませんでした"。
    - 日時バリデーションは本番どおり3段階: 書式/項目数不良 →
      "地震の発生日時の設定が不正です"、値の不良は「検索開始日」「検索
      終了日」「検索開始時刻」「検索終了時刻」の順に集約 →
      「…が不正です」、開始>終了 → "地震の発生日時の設定が逆転して
      います"。
    - `maxInt` コード (本番突き合わせで確認): `2/3/4` = 震度N以上、
      `A`/`B` = 震度５弱/５強以上、`C`/`D` = 震度６弱/６強以上、
      `7` = 震度7のみ。無効な値 (`1`/`5`/`6`/`9` 等) は無視。有効コード
      適用時は `str` に「最大震度 ： …」行が追加される(0件でも)。
    - `Sort` は `S0`(新しい順/デフォルト)/`S1`(古い順)/`S2`(最大震度の
      大きい順)/`S3`(規模の大きい順)を再現。裸数字や無効コードは S0 扱い。
      件数行の文言もソートに追従する。
  - `mode=event`: `res = {"hyp": [...], "int": [...]}`、キー順も本番どおり
    (int 行: name,lat,lon,code,int,char,mark,zidx)。
    id 指定なしは「検索に失敗しました」、未登録 id は
    `検索に失敗しました（震源）：{id}`。`Q[]` は本番同様に無視。
- `mode=int` や未知の mode は本番同様に **200 空ボディ**
  (content-type text/html; charset=UTF-8)。

## 簡略化(プロトタイプ時点)

- `obsInt` / `pref[]` / `city[]` / `station[]` / `epi[]` / `mag[]` / `dep[]`
  / `additionalC` / `Comp` / `seisCount` / `observed` / `strParam` は本番
  同様に**受けて無視**(本番でも無視されることを実測で確認済み)。
- `boundsAr[i][]` は緯度経度 bbox フィルタとして実装済み。
- スカラー値の `mag`/`dep`/`epi` は本番が 500 になるが、ローカルでは
  無視して応答する(文書化済みの許容差異)。
- 震度の数値化は `maxIcls`(`bg-I{N}` + `bg-IA..ID` を 5..8 のランクに
  正規化した CASE 式)から取得。

## 検証テスト

```sh
.venv/bin/pytest                      # ローカル契約テスト(ネットワーク不要)
RUN_LIVE_TESTS=1 .venv/bin/pytest     # + 本番 API 突き合わせ(ネットワーク必要)
```

本番突き合わせは「同じ時間窓の search 結果のキー集合一致」「サンプル地震
20190520212248 の hyp/int 値が本番と完全一致」「シードDBの地震集合は本番の
部分集合」を検証する。

## 今後のステップ(Step by step)

- [x] Step 1: API サーバ + SQLite スキーマ + テスト群
- [x] Step 2: 収集バッチ `scripts/collect_data.py`
- [x] Step 3: 資産ミラー `scripts/mirror_static.py`
- [x] Step 4a: データ収集 (下記のサンプル区間 + ブラウザ検証用データ)
- [x] Step 4b: ブラウザでの手動検証 (「ブラウザで確認する」参照)
- [ ] Step 5: (任意) pref/city/mag/dep フィルタの本格実装

## 現在 DB に入っているデータ (収集区間)

`data/shindo.db` は **地震 1,679 件 / 観測点震度行 126,720 行** を含む:

- 1995-01-16 .. 1995-01-18: 兵庫県南部(阪神・淡路)など 127 件
  (震度7 の 19950117054651 大阪湾 = 93 観測点行、S6 マーカーあり)
- 2011-03-10 .. 2011-03-12: 東北地方太平洋沖と余震など 1,265 件
  (震度7 の 20110311144618 三陸沖 = 2,901 観測点行)
- 2026-09-01 .. 2026-10-16: 最近の活動 283 件 (最新 2026-10-15 千葉県北西部)
- 2019-05-20: トカラ列島近海ほか 4 件

全部収集する場合は `scripts/collect_data.py` を引数なしで実行
(全期間、数時間かかる)。

## ブラウザで確認する (Step 4b)

サーバを起動していないなら:

```sh
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

ブラウザで <http://127.0.0.1:8000/> を開く (`/` は SPA の index へ
リダイレクトする)。地図表示は Leaflet + オンライン地図タイルのため、
PC 側はインターネットに接続しておくこと (データはすべてローカル DB)。

見所の例:

1. **東北の大地震でマーカーだらけの地図**: 期間
   `2011-03-11 00:00 ～ 2011-03-11 23:59`、震度 `7` を選んで「検索」。
   → 三陸沖 震度7 が 1 件ヒット (この検索は「震度7のみ」フィルタの
   特殊動作: str に `最大震度 ： 震度７` 行が入る)。クリックすると
   Leaflet 地図に震源 + 2,901 点の観測点マーカー。
2. **1995 年の震度6マーカー**: 期間 `1995-01-17 00:00 ～ 1995-01-17
   23:59`、震度 `6` を選んで検索 → 大阪湾 震度7 をクリックで S6/S5
   マーカー付き詳細。
3. **最近の地震リスト**: 期間 `2026-09-01 ～ 2026-10-16`、震度 `1`〜`6`
   → 283 件。任意の行をクリックして詳細地図。
4. 0件・日時不正・未知モードなどのエラーメッセージも本番どおり表示。

