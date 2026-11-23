"""HTTP API and browser demo for personal sign recognition.

Run with:  python -m personal_signs serve
Then open: http://127.0.0.1:8000

Who is calling is decided in this order:

1. **Accounts (PocketBase)** - when ``POCKETBASE_URL`` is set, every API call
   must send ``Authorization: Bearer <PocketBase login token>``. The token is
   checked with PocketBase and the account id becomes the user, so each person
   only sees their own signs. Use this when the server is on the internet.
2. **API key** - when ``PERSONAL_SIGNS_API_KEY`` is set, calls must send that
   key; ``X-User`` picks the profile.
3. **Local** - neither set: anyone who can reach the server uses ``X-User``
   (default "default"). Fine on your own computer.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from . import __version__, engine

logger = logging.getLogger(__name__)

API_KEY_ENV = "PERSONAL_SIGNS_API_KEY"
POCKETBASE_URL_ENV = "POCKETBASE_URL"  # how this server reaches PocketBase, e.g. http://127.0.0.1:8090
POCKETBASE_PUBLIC_URL_ENV = "POCKETBASE_PUBLIC_URL"  # what browsers use, e.g. https://db.signbridgeco.xyz
POCKETBASE_COLLECTION = "users"
USER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STATIC_DIR = Path(__file__).resolve().parent / "static"

# A checked login token is trusted for a short while so every request does not
# ask PocketBase again. Logging out in the browser simply forgets the token.
TOKEN_CACHE_SECONDS = 60
_token_cache: dict[str, tuple[str, float]] = {}
_token_cache_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("PERSONAL_SIGNS_WARM_UP", "1") == "1":
        engine.warm_up_extractors()
    yield
    engine.shutdown_extractors()


app = FastAPI(title="Personal ISL sign recognition", version=__version__, lifespan=lifespan)

# Websites allowed to use this server from the browser (comma-separated).
# allow_private_network lets a public site (e.g. signbridgeco.xyz) reach a
# server running on the visitor's own device (localhost), which Chrome
# otherwise blocks.
ALLOWED_ORIGINS_ENV = "PERSONAL_SIGNS_ALLOWED_ORIGINS"
DEFAULT_ALLOWED_ORIGINS = "https://signbridgeco.xyz,https://www.signbridgeco.xyz"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get(ALLOWED_ORIGINS_ENV, DEFAULT_ALLOWED_ORIGINS).split(",")
        if origin.strip()
    ],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "X-User", "Content-Type"],
    allow_private_network=True,
)


def _pocketbase_account(base_url: str, token: str) -> str:
    """The PocketBase account id for a login token, or 401 if it is not valid."""
    now = time.monotonic()
    with _token_cache_lock:
        cached = _token_cache.get(token)
        if cached and cached[1] > now:
            return cached[0]
    request = urllib.request.Request(
        f"{base_url}/api/collections/{POCKETBASE_COLLECTION}/auth-refresh",
        method="POST",
        headers={"Authorization": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - configured URL
            account = json.load(response)["record"]["id"]
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403, 404):
            raise HTTPException(status_code=401, detail="Please log in again.") from exc
        logger.warning("PocketBase answered %s while checking a login", exc.code)
        raise HTTPException(status_code=503, detail="The account server had a problem. Try again.") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        logger.warning("Could not check a login with PocketBase: %s", exc)
        raise HTTPException(status_code=503, detail="The account server is not reachable.") from exc
    if not USER_NAME.fullmatch(account):
        raise HTTPException(status_code=401, detail="Please log in again.")
    with _token_cache_lock:
        if len(_token_cache) > 1000:
            _token_cache.clear()
        _token_cache[token] = (account, now + TOKEN_CACHE_SECONDS)
    return account


async def _user(authorization: str | None, x_user: str | None) -> str:
    sent = (authorization or "").removeprefix("Bearer ").strip()

    pocketbase = os.environ.get(POCKETBASE_URL_ENV, "").strip().rstrip("/")
    if pocketbase:
        if not sent:
            raise HTTPException(status_code=401, detail="Please log in or sign up first.")
        return await run_in_threadpool(_pocketbase_account, pocketbase, sent)

    key = os.environ.get(API_KEY_ENV, "")
    if key and not hmac.compare_digest(sent.encode(), key.encode()):
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


@app.get("/config")
async def config():
    """How the page should sign people in (public; contains no secrets)."""
    pocketbase = os.environ.get(POCKETBASE_URL_ENV, "").strip()
    if pocketbase:
        public = os.environ.get(POCKETBASE_PUBLIC_URL_ENV, "").strip() or pocketbase
        return {"auth": "pocketbase", "pocketbase_url": public.rstrip("/")}
    return {"auth": "api_key" if os.environ.get(API_KEY_ENV) else "none"}


@app.get("/signs")
async def list_signs(authorization: str | None = Header(None), x_user: str | None = Header(None)):
    """The user's saved signs, with the gate result of every example."""
    user = await _user(authorization, x_user)
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
    user = await _user(authorization, x_user)
    clip = await request.body()
    return await _call(engine.save_sample, user, label, clip, kind, confirm)


@app.delete("/signs/{label}/{sample_id}")
async def delete_example(
    label: str, sample_id: str, authorization: str | None = Header(None), x_user: str | None = Header(None)
):
    """Undo one saved example and keep the trained model in sync."""
    return await _call(engine.delete_sample, await _user(authorization, x_user), label, sample_id)


@app.post("/train")
async def train(authorization: str | None = Header(None), x_user: str | None = Header(None)):
    """Build the user's model; with no clips yet, rebuild the seed model instead."""
    user = await _user(authorization, x_user)
    if engine.list_samples(user):
        return await _call(engine.train_user, user)
    if not engine.SEED_ROOT.is_dir():
        raise HTTPException(
            status_code=400,
            detail="No examples saved yet. Save at least 3 examples of 2 different signs, then press Train.",
        )
    return await _call(engine.train_seed_model)


@app.post("/recognize")
async def recognize(request: Request, authorization: str | None = Header(None), x_user: str | None = Header(None)):
    """Recognize one clip (request body = MP4/WebM bytes)."""
    user = await _user(authorization, x_user)
    return await _call(engine.predict_user, user, await request.body())
