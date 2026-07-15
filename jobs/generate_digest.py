"""Scheduled weekly digest: read the week's check-ins, summarize with the
serving endpoint, pin the result to <schema>.campfire_digests. The app
serves the pinned digest when it postdates the newest entry.

Runs as a bundle job on serverless compute (Spark for table IO, the job's
identity for model auth). Args: [0] catalog.schema, [1] serving endpoint.
"""

import sys
from datetime import datetime, timedelta, timezone

from databricks.sdk import WorkspaceClient
from pyspark.sql import SparkSession

SCHEMA = sys.argv[1] if len(sys.argv) > 1 else "main.default"
ENDPOINT = sys.argv[2] if len(sys.argv) > 2 else "databricks-claude-sonnet-4-5"

DIGEST_SYSTEM = (
    "You write a short digest of a team's check-in log for a given period. "
    "Surface themes and trends, call out wins, and always mention open help "
    "requests if any. Two or three plain sentences, warm but direct, no bullet "
    "points, no preamble. Reply with ONLY the digest text."
)


def week_range_label(now: datetime) -> str:
    start = now - timedelta(days=now.weekday())
    end = start + timedelta(days=4)
    if start.month == end.month:
        return f"{start.strftime('%b').upper()} {start.day}–{end.day}"
    return f"{start.strftime('%b').upper()} {start.day}–{end.strftime('%b').upper()} {end.day}"


def main():
    spark = SparkSession.builder.getOrCreate()
    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")

    rows = spark.sql(
        f"""SELECT author, category, text, open FROM {SCHEMA}.campfire_entries
            WHERE created_at >= '{week_start}' ORDER BY created_at DESC LIMIT 200"""
    ).collect()

    label = week_range_label(now)
    if not rows:
        text = "Nothing logged yet this week — the digest will fill in as check-ins land."
    else:
        log = "\n".join(
            f"- [{r.category}{' · OPEN' if r.open else ''}] {r.author}: {r.text}" for r in rows
        )
        w = WorkspaceClient()
        resp = w.api_client.do(
            "POST",
            f"/serving-endpoints/{ENDPOINT}/invocations",
            body={
                "messages": [
                    {"role": "system", "content": DIGEST_SYSTEM},
                    {"role": "user", "content": f"Period: this week ({label}).\nTeam check-in log:\n{log}"},
                ],
                "max_tokens": 400,
            },
        )
        text = resp["choices"][0]["message"]["content"].strip()

    spark.sql(
        f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.campfire_digests (
            period STRING, range_label STRING, text STRING, created_at TIMESTAMP)"""
    )
    spark.sql(
        f"INSERT INTO {SCHEMA}.campfire_digests VALUES ('Week', :label, :text, current_timestamp())",
        args={"label": label, "text": text},
    )
    print(f"Pinned Week digest ({label}): {text[:120]}")


if __name__ == "__main__":
    main()
