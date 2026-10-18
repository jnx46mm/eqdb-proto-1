#!/usr/bin/env python3
"""Mirror the static frontend assets of the JMA shindo site into ./static.

Reproduces the original path layout (static/eqdb/data/shindo/...) so the
absolute references inside assets/index.js keep working when app/main.py
hosts ./static at '/'.

Downloads:
  index.html, assets/index.js, assets/index.css,
  js/{city,date,epi,station}.json, and every images/... file referenced
  from assets/index.js (discovered automatically).

Usage: .venv/bin/python scripts/mirror_static.py
"""

import re
import sys
import urllib.request
from pathlib import Path

SITE = "https://www.data.jma.go.jp/eqdb/data/shindo"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static"

FIXED_PATHS = [
    "/index.html",
    "/assets/index.js",
    "/assets/index.css",
    "/js/city.json",
    "/js/date.json",
    "/js/epi.json",
    "/js/station.json",
]


def fetch(path: str) -> bytes:
    url = SITE + path
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def main() -> int:
    total = 0
    for p in FIXED_PATHS:
        data = fetch(p)
        dest = OUT / "eqdb/data/shindo" / p.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"{len(data):8d}  {dest}")
        total += len(data)

    bundle = (OUT / "eqdb/data/shindo/assets/index.js").read_text(
        encoding="utf-8", errors="replace"
    )
    img_paths = sorted(set(re.findall(r"(?:images/[\w.-]+?\.(?:svg|gif|jpg|png))", bundle)))
    for name in img_paths:
        p = "/" + name
        data = fetch(p)
        dest = OUT / "eqdb/data/shindo" / p.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"{len(data):8d}  {dest}")
        total += len(data)

    print(f"done: {total} bytes into {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
