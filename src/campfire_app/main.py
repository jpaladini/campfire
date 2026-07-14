import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import storage
from .ai import AI
from .identity import current_user
from .shipping import ShippingSync

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Campfire")
api = APIRouter(prefix="/api")

store = storage.get_store()
ai = AI()
shipping_sync = ShippingSync()

PERIODS = ("Day", "Week", "Month", "Year")
CATEGORIES = ("win", "loss", "help", "learned")


def period_start(period: str, now: datetime) -> datetime:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "Day":
        return day
    if period == "Week":
        return day - timedelta(days=day.weekday())
    if period == "Month":
        return day.replace(day=1)
    return day.replace(month=1, day=1)


def range_label(period: str, now: datetime) -> str:
    if period == "Day":
        return "TODAY"
    if period == "Week":
        start = now - timedelta(days=now.weekday())
        end = start + timedelta(days=4)
        if start.month == end.month:
            return f"{start.strftime('%b').upper()} {start.day}–{end.day}"
        return f"{start.strftime('%b').upper()} {start.day}–{end.strftime('%b').upper()} {end.day}"
    if period == "Month":
        return now.strftime("%B").upper()
    return str(now.year)


def entries_in_period(period: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    start = period_start(period, now)
    out = []
    for e in store.list_entries():
        try:
            created = datetime.fromisoformat(e["createdAt"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue
        if created >= start:
            out.append(e)
    return out


class CleanupBody(BaseModel):
    text: str


class EntryIn(BaseModel):
    category: str
    text: str


class SaveBody(BaseModel):
    entries: list[EntryIn]


class SettingsBody(BaseModel):
    reminders: bool
    autoClean: bool
    anonymousLosses: bool
    digestDay: str


@api.get("/health")
def health():
    return {"status": "ok", "ai": "live" if ai.live else "fallback"}


@api.get("/me")
def me(request: Request):
    return current_user(request)


@api.get("/feed")
def feed(period: str = "Week"):
    period = period if period in PERIODS else "Week"
    entries = entries_in_period(period)
    counts = {c: sum(1 for e in entries if e["category"] == c) for c in CATEGORIES}
    return {
        "entries": entries,
        "stats": {
            "wins": counts["win"],
            "losses": counts["loss"],
            "helpOpen": sum(1 for e in entries if e["category"] == "help" and e.get("open")),
            "learnings": counts["learned"],
        },
    }


@api.post("/entries")
def save_entries(body: SaveBody, request: Request):
    user = current_user(request)
    settings = store.get_settings(user["id"])
    created = []
    for item in body.entries:
        text = item.text.strip()
        if not text or item.category not in CATEGORIES:
            continue
        if settings.get("autoClean"):
            text = ai.cleanup(text)
        anon = settings.get("anonymousLosses") and item.category == "loss"
        created.append(
            storage.new_entry(
                author="Anonymous" if anon else user["name"],
                initials="?" if anon else user["initials"],
                avatar_color="#8a8578" if anon else user["avatarColor"],
                category=item.category,
                text=text,
                open_=item.category == "help",
            )
        )
    if created:
        store.add_entries(created)
    return {"added": len(created), "entries": created}


@api.post("/cleanup")
def cleanup(body: CleanupBody):
    return {"text": ai.cleanup(body.text)}


# Digest cache, keyed by what the digest is ABOUT: the period's range label,
# entry count and newest entry id. No TTL — a digest is recomputed only when
# the team log changes (save/resolve shifts the key), never per view.
_digest_cache: dict[str, dict] = {}


@api.get("/digest")
def digest(period: str = "Week"):
    period = period if period in PERIODS else "Week"
    now = datetime.now(timezone.utc)
    entries = entries_in_period(period)
    label = range_label(period, now)
    open_count = sum(1 for e in entries if e.get("open"))
    key = f"{label}:{len(entries)}:{open_count}:{entries[0]['id'] if entries else '-'}"

    hit = _digest_cache.get(period)
    if hit and hit["key"] == key:
        return hit["result"]

    # Prefer the scheduled job's pinned digest when it postdates the newest
    # entry (i.e. it already saw everything the live view sees).
    pinned = store.get_pinned_digest(period)
    if pinned and pinned["range"] == label:
        newest = entries[0]["createdAt"] if entries else None
        if not newest or pinned["createdAt"] >= newest:
            result = {"range": pinned["range"], "text": pinned["text"], "pinned": True}
            _digest_cache[period] = {"key": key, "result": result}
            return result

    result = {"range": label, "text": ai.digest(entries, period, label)}
    _digest_cache[period] = {"key": key, "result": result}
    return result


@api.post("/entries/{entry_id}/resolve")
def resolve_entry(entry_id: str, request: Request):
    user = current_user(request)
    if not store.resolve_entry(entry_id, user["name"]):
        raise HTTPException(status_code=404, detail="Not your open help entry")
    return {"resolved": entry_id}


@api.get("/shipping")
def shipping():
    return shipping_sync.get()


@api.get("/settings")
def get_settings(request: Request):
    return store.get_settings(current_user(request)["id"])


@api.put("/settings")
def put_settings(body: SettingsBody, request: Request):
    store.put_settings(current_user(request)["id"], body.model_dump())
    return body.model_dump()


app.include_router(api)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if (STATIC_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        candidate = STATIC_DIR / path
        if path and candidate.is_file() and candidate.resolve().is_relative_to(STATIC_DIR):
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
