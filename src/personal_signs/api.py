"""HTTP API and browser demo for personal sign recognition.

Run with:  python -m personal_signs serve
Then open: http://127.0.0.1:8000

Each request names its user with the `X-User` header (default "default").
Set PERSONAL_SIGNS_API_KEY to require `Authorization: Bearer <key>` on every
API call; always do this before putting the server on the internet.
"""

from __future__ import annotations

import hmac
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from . import __version__, engine

API_KEY_ENV = "PERSONAL_SIGNS_API_KEY"
USER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("PERSONAL_SIGNS_WARM_UP", "1") == "1":
        engine.warm_up_extractors()
    yield
    engine.shutdown_extractors()


app = FastAPI(title="Personal ISL sign recognition", version=__version__, lifespan=lifespan)


def _user(authorization: str | None, x_user: str | None) -> str:
    key = os.environ.get(API_KEY_ENV, "")
    if key:
        sent = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(sent.encode(), key.encode()):
            raise HTTPException(status_code=401, detail="Missing or wrong API key.")
    user = (x_user or "default").strip()
    if not USER_NAME.fullmatch(user):
        raise HTTPException(status_code=400, detail="X-User must be 1-64 letters, numbers, _ or -.")
    return user


async def _call(function, *args):
    try:
        return await run_in_threadpool(function, *args)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/", include_in_schema=False)
async def demo_page():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


@app.get("/signs")
async def list_signs(authorization: str | None = Header(None), x_user: str | None = Header(None)):
    """The user's saved signs, with the gate result of every example."""
    user = _user(authorization, x_user)
    return {
        "samples": await _call(engine.list_samples, user),
        "details": await _call(engine.list_sample_details, user),
    }


@app.post("/signs/{label}")
async def add_example(
    label: str,
    request: Request,
    kind: str | None = None,
    confirm: bool = False,
    authorization: str | None = Header(None),
    x_user: str | None = Header(None),
):
    """Gate a new clip (request body = MP4/WebM bytes); saved only when reliable or confirmed."""
    user = _user(authorization, x_user)
    clip = await request.body()
    return await _call(engine.save_sample, user, label, clip, kind, confirm)


@app.delete("/signs/{label}/{sample_id}")
async def delete_example(
    label: str, sample_id: str, authorization: str | None = Header(None), x_user: str | None = Header(None)
):
    """Undo one saved example and keep the trained model in sync."""
    return await _call(engine.delete_sample, _user(authorization, x_user), label, sample_id)


@app.post("/train")
async def train(authorization: str | None = Header(None), x_user: str | None = Header(None)):
    """Build the user's model; with no clips yet, rebuild the seed model instead."""
    user = _user(authorization, x_user)
    if engine.list_samples(user):
        return await _call(engine.train_user, user)
    return await _call(engine.train_seed_model)


@app.post("/recognize")
async def recognize(request: Request, authorization: str | None = Header(None), x_user: str | None = Header(None)):
    """Recognize one clip (request body = MP4/WebM bytes)."""
    user = _user(authorization, x_user)
    return await _call(engine.predict_user, user, await request.body())
