"""AI features: per-field cleanup and team digest via a Databricks
model-serving endpoint (Claude Sonnet). Falls back to a deterministic
local cleanup / heuristic digest when the endpoint is unreachable, so
the app degrades gracefully instead of erroring.
"""

import logging
import os
import re

logger = logging.getLogger("campfire.ai")

SERVING_ENDPOINT = os.environ.get("SERVING_ENDPOINT", "databricks-claude-sonnet-4-5")

CLEANUP_SYSTEM = (
    "You clean up short workplace check-in entries (wins, losses, help requests, "
    "learnings). Fix typos, casing, punctuation and filler words, but preserve the "
    "author's meaning and voice. Keep it to one or two sentences. Reply with ONLY "
    "the cleaned text — no quotes, no commentary."
)

DIGEST_SYSTEM = (
    "You write a short digest of a team's check-in log for a given period. "
    "Surface themes and trends, call out wins, and always mention open help "
    "requests if any. Two or three plain sentences, warm but direct, no bullet "
    "points, no preamble. Reply with ONLY the digest text."
)


def _workspace_client():
    try:
        from databricks.sdk import WorkspaceClient

        return WorkspaceClient()
    except Exception as e:  # no credentials / not in a Databricks context
        logger.warning("Databricks auth unavailable, AI falls back to local mode: %s", e)
        return None


class AI:
    def __init__(self):
        self._w = _workspace_client()

    @property
    def live(self) -> bool:
        return self._w is not None

    def _chat(self, system: str, user: str, max_tokens: int = 400) -> str | None:
        if self._w is None:
            return None
        try:
            resp = self._w.api_client.do(
                "POST",
                f"/serving-endpoints/{SERVING_ENDPOINT}/invocations",
                body={
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": max_tokens,
                },
            )
            return resp["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("Serving endpoint call failed, using fallback: %s", e)
            return None

    def cleanup(self, text: str) -> str:
        out = self._chat(CLEANUP_SYSTEM, text, max_tokens=300)
        return out if out else local_cleanup(text)

    def digest(self, entries: list[dict], period: str, range_label: str) -> str:
        if not entries:
            return f"Nothing logged yet this {period.lower()} — the digest will fill in as check-ins land."
        log = "\n".join(
            f"- [{e['category']}{' · OPEN' if e.get('open') else ''}] {e['author']}: {e['text']}"
            for e in entries[:80]
        )
        out = self._chat(
            DIGEST_SYSTEM,
            f"Period: this {period.lower()} ({range_label}).\nTeam check-in log:\n{log}",
            max_tokens=400,
        )
        return out if out else heuristic_digest(entries, period)


def local_cleanup(t: str) -> str:
    """Deterministic fallback mirroring the design prototype's mock."""
    s = re.sub(r"\s+", " ", t.strip())
    for pat, rep in [
        (r"\bteh\b", "the"), (r"\bdont\b", "don't"), (r"\bdidnt\b", "didn't"),
        (r"\bwasnt\b", "wasn't"), (r"\bcant\b", "can't"), (r"\bwont\b", "won't"),
    ]:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    s = re.sub(r"\bi\b", "I", s)
    s = re.sub(r"\s*\b(lol|lmao|tbh|haha)\b\s*", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"!{2,}", "!", s)
    s = re.sub(r"\.{2,}", ".", s)
    s = re.sub(r"\s+([.!?,])", r"\1", s)
    if s:
        s = s[0].upper() + s[1:]
        if not re.search(r"[.!?]$", s):
            s += "."
    return s


def heuristic_digest(entries: list[dict], period: str) -> str:
    wins = sum(1 for e in entries if e["category"] == "win")
    losses = sum(1 for e in entries if e["category"] == "loss")
    open_help = [e for e in entries if e["category"] == "help" and e.get("open")]
    learned = sum(1 for e in entries if e["category"] == "learned")
    parts = [f"This {period.lower()}: {wins} wins, {losses} losses, {learned} learnings logged."]
    if open_help:
        who = ", ".join(sorted({e["author"].split()[0] for e in open_help}))
        parts.append(f"{len(open_help)} help request{'s' if len(open_help) != 1 else ''} still open ({who}) — that's the thing to watch.")
    else:
        parts.append("No open help requests right now.")
    return " ".join(parts)
