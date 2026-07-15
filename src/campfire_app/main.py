import hashlib
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


def parse_date(date_str: str) -> datetime | None:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def entries_in_scope(period: str, date_str: str = "") -> list[dict]:
    """Entries for the period — or, when a specific date is picked, that
    single UTC calendar day (the date filter overrides the period)."""
    now = datetime.now(timezone.utc)
    day = parse_date(date_str) if date_str else None
    start = day if day else period_start(period, now)
    end = day + timedelta(days=1) if day else None
    out = []
    for e in store.list_entries():
        try:
            created = datetime.fromisoformat(e["createdAt"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue
        if created >= start and (end is None or created < end):
            out.append(e)
    return out


def scope_label(period: str, date_str: str, now: datetime) -> str:
    day = parse_date(date_str) if date_str else None
    if day:
        return f"{day.strftime('%b').upper()} {day.day}" + (f", {day.year}" if day.year != now.year else "")
    return range_label(period, now)


def entries_fingerprint(entries: list[dict]) -> str:
    blob = "|".join(f"{e['id']}:{e.get('open')}:{e['text']}" for e in entries)
    return hashlib.md5(blob.encode()).hexdigest()[:16]


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
def feed(request: Request, period: str = "Week", mine: bool = False, date: str = ""):
    period = period if period in PERIODS else "Week"
    entries = entries_in_scope(period, date)
    if mine:
        # Anonymous entries are unattributable by design, so a personal view
        # cannot include the viewer's own anonymous losses.
        name = current_user(request)["name"]
        entries = [e for e in entries if e["author"] == name]
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


# Digest cache, keyed by what each digest is ABOUT: one slot per
# (period, focus filter, viewer-scope), each slot keyed by that slice's range
# label, entry count, open count and newest entry id. No TTL — a digest is
# recomputed only when its slice of the log changes (save/resolve shifts the
# key), never per view.
_digest_cache: dict[str, dict] = {}

FOCUS_MAP = {"All": None, "Wins": "win", "Losses": "loss", "Help": "help", "Learned": "learned"}


@api.get("/digest")
def digest(request: Request, period: str = "Week", focus: str = "All", mine: bool = False, date: str = ""):
    period = period if period in PERIODS else "Week"
    cat = FOCUS_MAP.get(focus)
    now = datetime.now(timezone.utc)
    entries = entries_in_scope(period, date)
    viewer = current_user(request)["name"] if mine else None
    if viewer:
        entries = [e for e in entries if e["author"] == viewer]
    if cat:
        entries = [e for e in entries if e["category"] == cat]
    label = scope_label(period, date, now)
    slot = f"{period}:{cat or 'all'}:{viewer or 'team'}:{date or '-'}"
    key = f"{label}:{entries_fingerprint(entries)}"

    hit = _digest_cache.get(slot)
    if hit and hit["key"] == key:
        return hit["result"]

    # Prefer the scheduled job's pinned team digest when it postdates the
    # newest entry (it already saw everything the live view sees).
    if not cat and not viewer and not date:
        pinned = store.get_pinned_digest(period)
        if pinned and pinned["range"] == label:
            newest = entries[0]["createdAt"] if entries else None
            if not newest or pinned["createdAt"] >= newest:
                result = {"range": pinned["range"], "text": pinned["text"], "pinned": True}
                _digest_cache[slot] = {"key": key, "result": result}
                return result

    text_period = "Day" if date else period
    result = {"range": label, "text": ai.digest(entries, text_period, label, focus=cat)}
    _digest_cache[slot] = {"key": key, "result": result}
    return result


class EditBody(BaseModel):
    text: str


@api.put("/entries/{entry_id}")
def edit_entry(entry_id: str, body: EditBody, request: Request):
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Text cannot be empty")
    user = current_user(request)
    if not store.update_entry(entry_id, user["name"], text):
        raise HTTPException(status_code=404, detail="Not your entry")
    return {"updated": entry_id, "text": text}


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
