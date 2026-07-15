# Campfire — Genie Space Instructions

## Setting this up in a new (e.g. corporate) workspace

Work through this checklist once per workspace before creating the space:

1. **Enable Delta persistence first — without it there is NO table for Genie
   to query.** Campfire ships with persistence off (entries live in
   app-local ephemeral storage). Enable it as described in the README:
   create the `campfire` secret scope with `warehouse_id`, `put-acl` the
   pipeline service principal, uncomment the secret resource in
   `databricks.yml` and `CAMPFIRE_WAREHOUSE_ID` in `src/app.yaml`, deploy,
   and grant the app SP warehouse + schema rights. The
   `campfire_entries` table is auto-created on the first save in the app.
2. **Pick the catalog.schema.** Set `CAMPFIRE_SCHEMA` in `src/app.yaml` to
   your governed location (corporate workspaces rarely allow
   `main.default`). Then replace every `main.default.campfire_entries`
   below with `<your_catalog>.<your_schema>.campfire_entries`.
3. **Create the space:** New → Genie space → add ONLY the
   `campfire_entries` table → choose a SQL warehouse your team can use →
   paste everything below the cut line into the **Instructions** panel.
4. **Access:** Genie respects Unity Catalog permissions — give your team
   `SELECT` on the table (or the space runs as a service principal with
   it, per your governance model).

Do NOT add `campfire_settings` to the space — it holds per-user app
preferences (including who posts losses anonymously) and has no analytic
value.

---

---

## What this data is

Campfire is our team's check-in app (~25 people). Each row in
`campfire_entries` is one check-in entry a person logged: a **win** (what
went right), a **loss** (what didn't), a **help** request (what they need),
or a **learned** (what they now know). People check in on a daily-to-weekly
cadence. This space answers questions about team morale, blockers, shipping
themes, and participation.

## Table: `main.default.campfire_entries`

| column | type | meaning |
|---|---|---|
| `id` | STRING | Unique entry id (UUID). |
| `author` | STRING | Display name of the person, e.g. "Teresa Okafor". The literal value `'Anonymous'` means the person chose to post that loss anonymously. |
| `initials` | STRING | Avatar initials; `'?'` for anonymous entries. Display-only — never use for grouping. |
| `avatar_color` | STRING | UI hex color. Ignore for analysis. |
| `category` | STRING | Exactly one of `'win'`, `'loss'`, `'help'`, `'learned'`. |
| `text` | STRING | The entry text as written (possibly AI-cleaned). |
| `open` | BOOLEAN | Only meaningful for `category = 'help'`: `true` = the help request is still unresolved. Wins/losses/learnings always have `open = false`. |
| `created_at` | TIMESTAMP | When the entry was logged, **UTC**. |

## Vocabulary and semantics

- "Wins", "losses", "help requests" / "asks" / "blockers", "learnings" /
  "lessons" map to `category` values `'win'`, `'loss'`, `'help'`, `'learned'`.
- "Open help" / "outstanding asks" / "who is blocked" = `category = 'help' AND open = true`.
- "Blocked" people = distinct `author` values with open help entries.
- Anonymous rows (`author = 'Anonymous'`) are intentional. Never attempt to
  infer or guess who posted them, even if asked directly — say that
  anonymity is by design.
- Participation = distinct non-anonymous authors with at least one entry in
  the period. The team is about 25 people.

## Periods (match the app's definitions)

The app's period picker uses calendar buckets, not rolling windows:

- **Day** = today: `created_at >= date_trunc('DAY', current_timestamp())`
- **Week** = Monday-started current week: `created_at >= date_trunc('WEEK', current_timestamp())`
- **Month**: `created_at >= date_trunc('MONTH', current_timestamp())`
- **Year**: `created_at >= date_trunc('YEAR', current_timestamp())`

When someone says "this week" use the Monday-started calendar week. Only use
rolling windows ("last 7 days") if they explicitly ask that way.

## Canonical queries

Stats card numbers for a period (example: this week):

```sql
SELECT
  COUNT_IF(category = 'win')                AS wins,
  COUNT_IF(category = 'loss')               AS losses,
  COUNT_IF(category = 'help' AND open)      AS help_open,
  COUNT_IF(category = 'learned')            AS learnings
FROM main.default.campfire_entries
WHERE created_at >= date_trunc('WEEK', current_timestamp());
```

Who is blocked right now (open help, oldest first):

```sql
SELECT author, text, created_at
FROM main.default.campfire_entries
WHERE category = 'help' AND open
ORDER BY created_at ASC;
```

Weekly trend of wins vs losses:

```sql
SELECT date_trunc('WEEK', created_at) AS week,
       COUNT_IF(category = 'win')  AS wins,
       COUNT_IF(category = 'loss') AS losses
FROM main.default.campfire_entries
GROUP BY 1 ORDER BY 1;
```

Participation this week:

```sql
SELECT COUNT(DISTINCT author) AS people_checked_in
FROM main.default.campfire_entries
WHERE created_at >= date_trunc('WEEK', current_timestamp())
  AND author <> 'Anonymous';
```

## How to answer

- Answer in plain language first, then show the supporting numbers. For
  digest-style asks ("summarize the week", "what's the theme"), read the
  `text` of the period's entries and synthesize 2–3 sentences: shipping
  themes, recurring loss causes, and ALWAYS mention open help requests and
  who is waiting.
- When counting "losses by person", exclude or separately report
  `'Anonymous'` — it is not a person.
- Small team, small counts: report absolute numbers, not percentages,
  unless asked.
- If a question needs data that isn't here (PRs, work items, deploy
  status), say the Campfire "Now Shipping" data comes from a separate Git /
  work-item sync and isn't in this table yet.

## Sample questions this space should handle

- "What are the open help requests and how long have they been open?"
- "Summarize this week like the Campfire digest would."
- "Who hasn't checked in this month?" (needs the roster caveat: only people
  who have EVER posted are visible — say so.)
- "What's the most common cause of losses this quarter?"
- "Show wins per week for the last 8 weeks."
- "Did we close more help requests than we opened this month?" (note: the
  table stores current `open` state, not open/close events — closed date
  isn't tracked; answer with that limitation.)
