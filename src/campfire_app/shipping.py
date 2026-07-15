"""Now Shipping: summarize recent pull-request activity from Azure DevOps.

Configured via env (ADO_ORG_URL, ADO_PROJECT, ADO_PAT — the PAT arrives
through a bundle secret resource). When unconfigured or unreachable, serves
clearly-flagged sample rows so the card never breaks. Results are cached;
`syncedMinutesAgo` reflects the real last successful sync.

Work-item sync is a future addition; the API shape here won't change.
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger("campfire.shipping")

TTL_SECONDS = 600
STALE_REVIEW_DAYS = 2
RECENT_DAYS = 7

OLIVE, OCHRE, NEUTRAL = "#7a8450", "#b8912f", "#6b6555"

SAMPLE = {
    "syncedMinutesAgo": 12,
    "sample": True,
    "rows": [
        {"color": OLIVE, "project": "Ingestion pipeline v2", "status": "3 PRs merged this week, 1 in review (Marcus, Jordan)"},
        {"color": OCHRE, "project": "Unity Catalog permissions", "status": "1 PR blocked on review 2 days (Priya)"},
        {"color": NEUTRAL, "project": "Latency investigation", "status": "2 open work items, no commits yet (Dana)"},
    ],
}


def _first_names(names: list[str], limit: int = 3) -> str:
    uniq = list(dict.fromkeys(n.split()[0] for n in names if n))
    shown = ", ".join(uniq[:limit])
    return shown + (f" +{len(uniq) - limit}" if len(uniq) > limit else "")


class ShippingSync:
    def __init__(self):
        self.org_url = os.environ.get("ADO_ORG_URL", "").rstrip("/")
        self.project = os.environ.get("ADO_PROJECT", "")
        self.pat = os.environ.get("ADO_PAT", "")
        self._lock = threading.Lock()
        self._cached: dict | None = None
        self._synced_at: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.org_url and self.project and self.pat)

    def _get(self, path: str, **params) -> dict:
        params.setdefault("api-version", "7.1")
        r = requests.get(
            f"{self.org_url}/{self.project}/_apis/{path}",
            auth=("", self.pat),
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def _fetch_rows(self) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
        now = datetime.now(timezone.utc)
        prs = self._get(
            "git/pullrequests",
            **{"searchCriteria.status": "all", "$top": 100},
        ).get("value", [])

        by_repo: dict[str, dict] = {}
        for pr in prs:
            repo = pr.get("repository", {}).get("name", "?")
            b = by_repo.setdefault(repo, {"merged": [], "active": [], "stale": []})
            author = pr.get("createdBy", {}).get("displayName", "")
            status = pr.get("status")
            if status == "completed":
                closed = pr.get("closedDate")
                if closed and datetime.fromisoformat(closed.replace("Z", "+00:00")) >= cutoff:
                    b["merged"].append(author)
            elif status == "active":
                created = pr.get("creationDate")
                age_days = 0
                if created:
                    age_days = (now - datetime.fromisoformat(created.replace("Z", "+00:00"))).days
                (b["stale"] if age_days >= STALE_REVIEW_DAYS else b["active"]).append(
                    {"author": author, "age": age_days}
                )

        rows = []
        for repo, b in by_repo.items():
            if not (b["merged"] or b["active"] or b["stale"]):
                continue
            bits, people = [], []
            if b["merged"]:
                bits.append(f"{len(b['merged'])} PR{'s' if len(b['merged']) != 1 else ''} merged this week")
                people += b["merged"]
            if b["active"]:
                bits.append(f"{len(b['active'])} in review")
                people += [p["author"] for p in b["active"]]
            if b["stale"]:
                oldest = max(p["age"] for p in b["stale"])
                bits.append(f"{len(b['stale'])} blocked on review {oldest} day{'s' if oldest != 1 else ''}")
                people += [p["author"] for p in b["stale"]]
            who = _first_names(people)
            color = OCHRE if b["stale"] else (OLIVE if b["merged"] else NEUTRAL)
            rows.append({
                "color": color,
                "project": repo,
                "status": ", ".join(bits) + (f" ({who})" if who else ""),
                "_sort": (0 if b["stale"] else 1, -len(b["merged"])),
            })
        rows.sort(key=lambda r: r.pop("_sort"))
        return rows[:6]

    def get(self) -> dict:
        if not self.configured:
            return SAMPLE
        with self._lock:
            age = time.time() - self._synced_at
            if self._cached is not None and age < TTL_SECONDS:
                return {**self._cached, "syncedMinutesAgo": int(age // 60)}
            try:
                rows = self._fetch_rows()
                self._cached = {"rows": rows, "sample": False}
                self._synced_at = time.time()
                return {**self._cached, "syncedMinutesAgo": 0}
            except Exception as e:
                logger.warning("ADO sync failed: %s", e)
                if self._cached is not None:
                    return {**self._cached, "syncedMinutesAgo": int(age // 60)}
                return SAMPLE
