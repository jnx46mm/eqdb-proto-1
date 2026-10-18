"""Uvicorn entry point for the local shindo data server.

Routes:
  POST /eqdb/data/shindo/api/   -- same path as the live site (the SPA's
                                   relative fetch("api/") resolves here)
  POST /api/                    -- convenience alias
  GET  /health                  -- liveness + db path
  GET  /                        -- static frontend (if ./static exists,
                                   mirrored via scripts/mirror_static.py)
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api import router, SPA_DIR
from .db import get_db_path, init_db

app = FastAPI(title="eqdb-proto-1 local shindo data server")
app.include_router(router)
init_db()  # create schema/data dir if missing

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STATIC_DIR = Path(os.environ.get("APP_STATIC_DIR", str(_REPO_ROOT / "static")))


@app.get("/")
async def root() -> RedirectResponse:
    """Convenience redirect to the SPA page (when static is mounted)."""
    return RedirectResponse(SPA_DIR + "/index.html", status_code=307)


if _STATIC_DIR.exists():
    # Mounted AFTER the API routes and the root redirect, so both win.
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "db": get_db_path(), "static": str(_STATIC_DIR)}
