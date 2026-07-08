"""Persistence for entries and per-user settings.

Primary backend: Delta tables via a Databricks SQL warehouse (statement
execution API), selected when CAMPFIRE_WAREHOUSE_ID is set — which the
bundle wires from a per-workspace secret. Fallback: a local JSON file
(ephemeral) so the app runs anywhere for development and demos.
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("campfire.storage")

SCHEMA = os.environ.get("CAMPFIRE_SCHEMA", "main.default")
ENTRIES_TABLE = f"{SCHEMA}.campfire_entries"
SETTINGS_TABLE = f"{SCHEMA}.campfire_settings"

DEFAULT_SETTINGS = {
    "reminders": True,
    "autoClean": False,
    "anonymousLosses": False,
    "digestDay": "Friday",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalStore:
    """JSON-file store. Ephemeral on Databricks Apps — dev/demo only."""

    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("CAMPFIRE_DATA_FILE", "data/campfire.json")
        self._lock = threading.Lock()
        self._data = {"entries": [], "settings": {}}
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self._data = json.load(f)
            except Exception:
                logger.warning("Could not read %s, starting empty", self.path)

    def _flush(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f)

    def add_entries(self, entries: list[dict]):
        with self._lock:
            self._data["entries"] = entries + self._data["entries"]
            self._flush()

    def list_entries(self, limit: int = 500) -> list[dict]:
        with self._lock:
            return list(self._data["entries"][:limit])

    def get_settings(self, user_id: str) -> dict:
        return {**DEFAULT_SETTINGS, **self._data["settings"].get(user_id, {})}

    def put_settings(self, user_id: str, settings: dict):
        with self._lock:
            self._data["settings"][user_id] = settings
            self._flush()


class WarehouseStore:
    """Delta tables through the SQL statement execution API."""

    def __init__(self, warehouse_id: str):
        from databricks.sdk import WorkspaceClient

        self.w = WorkspaceClient()
        self.warehouse_id = warehouse_id
        self._ensure_tables()

    def _sql(self, statement: str, params: dict | None = None) -> list[list]:
        from databricks.sdk.service.sql import StatementParameterListItem

        parameters = [
            StatementParameterListItem(name=k, value=str(v) if v is not None else None)
            for k, v in (params or {}).items()
        ]
        resp = self.w.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id,
            statement=statement,
            parameters=parameters or None,
            wait_timeout="30s",
        )
        state = resp.status.state.value if resp.status and resp.status.state else "UNKNOWN"
        if state != "SUCCEEDED":
            raise RuntimeError(f"SQL {state}: {resp.status.error.message if resp.status and resp.status.error else statement[:80]}")
        return resp.result.data_array if resp.result and resp.result.data_array else []

    def _ensure_tables(self):
        self._sql(
            f"""CREATE TABLE IF NOT EXISTS {ENTRIES_TABLE} (
                id STRING, author STRING, initials STRING, avatar_color STRING,
                category STRING, text STRING, open BOOLEAN, created_at TIMESTAMP)"""
        )
        self._sql(
            f"""CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE} (
                user_id STRING, settings STRING, updated_at TIMESTAMP)"""
        )

    def add_entries(self, entries: list[dict]):
        for e in entries:
            self._sql(
                f"""INSERT INTO {ENTRIES_TABLE} VALUES
                    (:id, :author, :initials, :avatar_color, :category, :text,
                     CAST(:open AS BOOLEAN), CAST(:created_at AS TIMESTAMP))""",
                {
                    "id": e["id"], "author": e["author"], "initials": e["initials"],
                    "avatar_color": e["avatarColor"], "category": e["category"],
                    "text": e["text"], "open": e["open"], "created_at": e["createdAt"],
                },
            )

    def list_entries(self, limit: int = 500) -> list[dict]:
        rows = self._sql(
            f"""SELECT id, author, initials, avatar_color, category, text, open,
                       date_format(created_at, "yyyy-MM-dd'T'HH:mm:ssxxx")
                FROM {ENTRIES_TABLE} ORDER BY created_at DESC LIMIT {int(limit)}"""
        )
        return [
            {
                "id": r[0], "author": r[1], "initials": r[2], "avatarColor": r[3],
                "category": r[4], "text": r[5], "open": r[6] in (True, "true", "TRUE"),
                "createdAt": r[7],
            }
            for r in rows
        ]

    def get_settings(self, user_id: str) -> dict:
        rows = self._sql(
            f"SELECT settings FROM {SETTINGS_TABLE} WHERE user_id = :user_id ORDER BY updated_at DESC LIMIT 1",
            {"user_id": user_id},
        )
        stored = json.loads(rows[0][0]) if rows else {}
        return {**DEFAULT_SETTINGS, **stored}

    def put_settings(self, user_id: str, settings: dict):
        self._sql(f"DELETE FROM {SETTINGS_TABLE} WHERE user_id = :user_id", {"user_id": user_id})
        self._sql(
            f"INSERT INTO {SETTINGS_TABLE} VALUES (:user_id, :settings, current_timestamp())",
            {"user_id": user_id, "settings": json.dumps(settings)},
        )


def new_entry(author: str, initials: str, avatar_color: str, category: str, text: str, open_: bool) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "author": author,
        "initials": initials,
        "avatarColor": avatar_color,
        "category": category,
        "text": text,
        "open": open_,
        "createdAt": utcnow(),
    }


def get_store():
    warehouse_id = os.environ.get("CAMPFIRE_WAREHOUSE_ID", "").strip()
    if warehouse_id:
        try:
            return WarehouseStore(warehouse_id)
        except Exception as e:
            logger.warning("Warehouse store unavailable (%s); using local JSON store", e)
    else:
        logger.info("CAMPFIRE_WAREHOUSE_ID not set; using local JSON store")
    return LocalStore()
