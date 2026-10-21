"""FastAPI app: search API + static mock storefront.

Run: .venv/bin/uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .pipeline import ENGINES, Pipeline

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="VIP Advanced Search Prototype")
pipe = Pipeline()


@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    engine: str = "jev",
    fastpath: bool = False,
    rerank: bool = False,
    cache: bool = True,
    year: int | None = None,
    make: str | None = None,
    model: str | None = None,
):
    if engine not in ENGINES:
        raise HTTPException(400, f"engine must be one of {ENGINES}")
    ymm = (year, make, model) if make and model else None
    return await pipe.search(q, engine, fastpath=fastpath, rerank=rerank, use_cache=cache, ymm=ymm)


@app.get("/api/suggest")
async def suggest(
    q: str = Query(..., min_length=1, max_length=200),
    engine: str = "jev",
    fastpath: bool = False,
    cache: bool = True,
    year: int | None = None,
    make: str | None = None,
    model: str | None = None,
):
    if engine not in ENGINES:
        raise HTTPException(400, f"engine must be one of {ENGINES}")
    ymm = (year, make, model) if make and model else None
    return await pipe.suggest(q, engine, fastpath=fastpath, use_cache=cache, ymm=ymm)


@app.get("/api/status")
async def status():
    return pipe.status()


@app.get("/api/ymm")
async def ymm():
    """Year -> make -> models, feeding the picker."""
    tree: dict[int, dict[str, list[str]]] = {}
    for (mk, md), years in pipe.cat.models.items():
        for y in years:
            tree.setdefault(y, {}).setdefault(mk, []).append(md)
    return {str(y): {mk: sorted(mds) for mk, mds in sorted(tree[y].items())} for y in sorted(tree, reverse=True)}


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------------------------------------------------------------------------
# Public deployment: serve everything under /vip (e.g. http://<ip>:8090/vip/) with a per-visitor
# rate limit on the API, since there's no reverse proxy in front. Run: uvicorn app.main:public
# ---------------------------------------------------------------------------
import time as _time  # noqa: E402
from collections import defaultdict as _dd  # noqa: E402

from fastapi import Request  # noqa: E402
from fastapi.responses import JSONResponse, RedirectResponse  # noqa: E402

public = FastAPI(title="VIP Advanced Search Prototype (public)")
RATE, BURST = 10.0, 60.0  # requests per second per IP, bucket size
_buckets: dict[str, list[float]] = _dd(lambda: [BURST, _time.monotonic()])


@public.middleware("http")
async def _rate_limit(request: Request, call_next):
    if request.url.path.startswith("/vip/api/"):
        ip = request.client.host if request.client else "?"
        tokens, last = _buckets[ip]
        now = _time.monotonic()
        tokens = min(BURST, tokens + (now - last) * RATE)
        if tokens < 1:
            _buckets[ip] = [tokens, now]
            return JSONResponse({"detail": "Too many requests"}, status_code=429)
        _buckets[ip] = [tokens - 1, now]
    return await call_next(request)


@public.get("/")
@public.get("/vip")
async def _to_vip():
    return RedirectResponse("/vip/")


public.mount("/vip", app)
