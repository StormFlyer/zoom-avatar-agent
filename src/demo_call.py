"""Public "Call Tessa" demo: /call page and its token endpoint.

Anyone with the link opens /call, presses the button, and the browser joins a
fresh LiveKit room of its own. The token carries a room configuration that
dispatches the normal agent (AGENT_NAME) into that room in "browser" mode, so
there is no meeting to join: the visitor talks to the avatar directly.

Off unless DEMO_ENABLED=1. Guards, all from env (read per request so a Coolify
change plus restart is all it takes):
    DEMO_KEY              if set, the link must carry ?k=<key>; change it to revoke
    DEMO_MAX_SECONDS      hard length of one call (default 600)
    DEMO_MAX_CONCURRENT   calls at once across everyone (default 2)
    DEMO_CALLS_PER_IP     calls one address may start ... (default 3)
    DEMO_IP_WINDOW_SECONDS ... per this window (default 3600)
    DEMO_PRESET / DEMO_IMAGE / DEMO_BOT_NAME  which avatar takes the call
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import secrets
import threading
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

_ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = pathlib.Path(__file__).with_name("demo_call.html")
AVATAR_DIR = _ROOT / "avatars"

# Routes that skip the panel login. Everything else stays behind PANEL_PASSWORD.
PUBLIC_PATHS = {"/call", "/call/poster", "/api/call/token"}

logger = logging.getLogger(__name__)
router = APIRouter()

_lock = threading.Lock()
_active: dict[str, float] = {}  # room name -> time its slot frees up
_by_ip: dict[str, deque] = {}  # ip -> start times inside the window


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def enabled() -> bool:
    return os.getenv("DEMO_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _key_ok(given: str) -> bool:
    key = os.getenv("DEMO_KEY", "")
    return not key or secrets.compare_digest(given.encode(), key.encode())


def _client_ip(request: Request) -> str:
    # Behind Coolify's proxy the socket peer is the proxy; the proxy appends the
    # real address as the last X-Forwarded-For entry (earlier ones are whatever
    # the client claimed, so they are not trusted).
    if os.getenv("BEHIND_PROXY", "").strip().lower() in {"1", "true", "yes", "on"}:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _reserve(ip: str, room: str, now: float) -> str | None:
    """Take a call slot. Returns a refusal reason, or None if granted."""
    max_seconds = _int("DEMO_MAX_SECONDS", 600)
    window = _int("DEMO_IP_WINDOW_SECONDS", 3600)
    with _lock:
        for r, until in list(_active.items()):
            if until <= now:
                del _active[r]
        starts = _by_ip.setdefault(ip, deque())
        while starts and starts[0] <= now - window:
            starts.popleft()
        if len(starts) >= _int("DEMO_CALLS_PER_IP", 3):
            return "rate"
        if len(_active) >= _int("DEMO_MAX_CONCURRENT", 2):
            return "busy"
        starts.append(now)
        # Slot held for the whole allowed length plus join time. A visitor who
        # hangs up early still holds it until then: simple, and errs on cost.
        _active[room] = now + max_seconds + 60
    return None


def reset() -> None:
    """Forget all calls (tests)."""
    with _lock:
        _active.clear()
        _by_ip.clear()


def _metadata(max_seconds: int) -> str:
    meta: dict = {
        "mode": "browser",
        "bot_name": os.getenv("DEMO_BOT_NAME", "").strip() or "Tessa",
        "maxSeconds": max_seconds,
        "joinDelay": 1,
        "requireAddress": False,
        "frame": _frame(),
    }
    if os.getenv("DEMO_PRESET", "").strip():
        meta["preset"] = os.getenv("DEMO_PRESET").strip()
    if os.getenv("DEMO_IMAGE", "").strip() and meta["frame"] == "portrait":
        meta["image"] = os.getenv("DEMO_IMAGE").strip()  # wide uses AVATAR_IMAGE_WIDE
    return json.dumps(meta)


def _frame() -> str:
    f = os.getenv("DEMO_FRAME", "").strip().lower() or os.getenv("AVATAR_FRAME", "").strip().lower()
    return f if f in {"portrait", "wide"} else "portrait"


def _make_token(room: str, identity: str, metadata: str) -> str:
    from livekit import api

    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(identity)
        .with_name("Guest")
        # Only good for joining for two minutes; the call itself is capped by the agent.
        .with_ttl(datetime.timedelta(seconds=120))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=False,
                can_publish_sources=["microphone"],
            )
        )
        .with_room_config(
            api.RoomConfiguration(
                name=room,
                empty_timeout=30,
                departure_timeout=10,
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=os.getenv("AGENT_NAME", "zoom-bot"), metadata=metadata
                    )
                ],
            )
        )
    )
    return token.to_jwt()


@router.get("/call", response_class=HTMLResponse)
async def call_page(k: str = "") -> HTMLResponse:
    if not enabled() or not _key_ok(k):
        raise HTTPException(404)
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@router.get("/call/poster")
async def call_poster(k: str = "") -> FileResponse:
    path = _demo_image_path()
    if not enabled() or not _key_ok(k) or path is None:
        raise HTTPException(404)
    return FileResponse(path)


def _demo_image_path() -> pathlib.Path | None:
    """The demo's face, or None if it isn't set or isn't in avatars/.
    Without it the agent would fall back to the first face in the folder."""
    img = os.getenv("DEMO_IMAGE", "").strip()
    if _frame() == "wide" and os.getenv("AVATAR_IMAGE_WIDE", "").strip():
        img = os.getenv("AVATAR_IMAGE_WIDE").strip()
    name = pathlib.Path(img).name
    path = AVATAR_DIR / name if name else None
    return path if path is not None and path.is_file() else None


@router.post("/api/call/token")
async def call_token(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not enabled() or not _key_ok(str((body or {}).get("k") or "")):
        raise HTTPException(404)
    missing = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") if not os.getenv(k)]
    if missing:
        return JSONResponse({"error": "Calls are not set up on this server."}, status_code=503)
    if _demo_image_path() is None:
        logger.warning("Demo call refused: DEMO_IMAGE (or AVATAR_IMAGE_WIDE) is not a file in avatars/")
        return JSONResponse({"error": "Calls are not set up on this server."}, status_code=503)

    room = f"demo-{secrets.token_hex(8)}"
    refused = _reserve(_client_ip(request), room, time.time())
    if refused == "rate":
        return JSONResponse(
            {"error": "You've reached the call limit for now. Please try again later."},
            status_code=429,
        )
    if refused == "busy":
        return JSONResponse(
            {"error": "Tessa is on other calls right now. Please try again in a few minutes."},
            status_code=503,
        )
    max_seconds = _int("DEMO_MAX_SECONDS", 600)
    token = _make_token(room, f"guest-{secrets.token_hex(4)}", _metadata(max_seconds))
    return JSONResponse(
        {"url": os.environ["LIVEKIT_URL"], "token": token, "room": room,
         "maxSeconds": max_seconds, "frame": _frame()}
    )
