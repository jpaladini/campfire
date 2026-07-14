# Campfire 🔥

Team wins & losses check-in app for a ~25-person team, deployed as a
**Databricks App** (React SPA + FastAPI). Each person logs **Wins, Losses,
Help needed, and Learnings** on a day/week/month/year cadence. Claude Sonnet
(via Databricks Foundation Model APIs) cleans up entries on demand and writes
the team digest.

## Architecture

```
frontend/            Vite + React SPA (design-tokens CSS, no UI framework)
src/                 The Databricks App (bundle source_code_path)
├── app.yaml         App entrypoint + env (identical across ALL environments)
├── serve.py         uvicorn launcher (respects DATABRICKS_APP_PORT)
├── requirements.txt
├── static/          COMMITTED SPA build (vite build → here)
└── campfire_app/
    ├── main.py      FastAPI: /api/health|me|feed|entries|cleanup|digest|shipping|settings
    ├── ai.py        Sonnet serving-endpoint client + local fallbacks
    ├── storage.py   Delta tables via SQL warehouse, or local JSON fallback
    └── identity.py  User from Databricks Apps forwarded headers (no name field)
databricks.yml       Asset Bundle: targets dev/stg/prod, app + secret resources
azure-pipelines.yml  Deploy on merge to dev/stg/prod (branch == target == env)
.github/workflows/mirror-to-ado.yml   GitHub → ADO mirror (the ONLY GitHub piece)
```

### How it behaves

- **Identity** comes from the Databricks App session headers
  (`X-Forwarded-Email` / `X-Forwarded-User`) — there is no name field.
- **AI**: `SERVING_ENDPOINT` (default `databricks-claude-sonnet-4-5`) is called
  with the app's own service principal. `/api/cleanup` rewrites one entry;
  `/api/digest` summarizes the selected period (cached 10 min). If the endpoint
  is unreachable the app degrades to a deterministic local cleanup and a
  heuristic digest — it never errors out.
- **Storage**: if `CAMPFIRE_WAREHOUSE_ID` resolves (per-workspace secret), the
  app auto-creates and uses Delta tables `campfire_entries` /
  `campfire_settings` in `CAMPFIRE_SCHEMA`. Otherwise it falls back to a local
  JSON file (ephemeral — dev/demo only).
- **Now Shipping** summarizes recent pull-request activity from the Azure
  DevOps project set in `src/app.yaml` (`ADO_ORG_URL`/`ADO_PROJECT`, PAT via
  the `campfire/ado_pat` secret): merged-this-week, in-review, and
  blocked->2-days buckets per repo, cached 10 min. Unconfigured or
  unreachable → clearly-flagged sample rows. Work items are a future add.
- **Digest caching**: digests are recomputed only when the team log changes
  (save/resolve), never per view. A scheduled bundle job
  (`campfire-weekly-digest`, Monday 06:00 UTC) additionally pins the weekly
  digest to `campfire_digests`; the app serves the pin while it postdates
  the newest entry.
- **Help lifecycle**: help entries are created open; the author sees a
  "✓ resolve" action on their own open items.

## Local development

```bash
# backend (terminal 1) — local JSON store, local AI fallback
cd src && pip install -r requirements.txt && python serve.py

# frontend with hot reload (terminal 2) — proxies /api to :8000
cd frontend && npm install && npm run dev

# production-style: build the SPA into src/static, serve everything from :8000
cd frontend && npm run build
```

Set `CAMPFIRE_DEV_USER=you@example.com` to fake an identity locally. To hit
real Databricks services locally, export `DATABRICKS_HOST` + `DATABRICKS_TOKEN`
(or a configured profile) and optionally `CAMPFIRE_WAREHOUSE_ID`.

The SPA build in `src/static/` is **committed** so the bundle can deploy with
zero Node toolchain; the pipeline rebuilds it anyway. After changing the
frontend, re-run `npm run build` and commit the result.

## Deployment (CI/CD)

Flow (see the runbook this repo follows):

```
git push (GitHub, any feature branch)
  → GitHub Action mirrors branch → Azure DevOps Repos
    → open PR branch → dev INSIDE ADO
      → human merges (protected branch — the one human gate)
        → Azure Pipeline: npm build → databricks bundle validate/deploy -t dev → bundle run
          → Databricks App updated (~3.5 min)
```

Promotion is a pure code merge: `dev` → `stg` → `prod`. Branch name == bundle
target == environment; the bundle and `src/app.yaml` are byte-identical across
branches. The only per-environment difference is secret **values**.

### One-time setup per workspace (BEFORE the first deploy)

```bash
# 1. Secret scope + values the bundle requires:
databricks secrets create-scope campfire -p <env-profile>
databricks secrets put-secret campfire warehouse_id --string-value "<sql-warehouse-id>" -p <env-profile>
databricks secrets put-secret campfire ado_pat --string-value "<ado-code-read-pat>" -p <env-profile>
# 2. IMPORTANT: the scope creator gets sole access — also grant the PIPELINE's
#    service principal, or the deploy fails with "secret does not exist":
databricks secrets put-acl campfire <pipeline-sp-application-id> MANAGE -p <env-profile>
```

After the first deploy (the app + job identities now exist):

- App service principal: CAN_QUERY on the serving endpoint, CAN_USE on the
  SQL warehouse, `USE CATALOG` + `USE SCHEMA, CREATE TABLE, SELECT, MODIFY`
  on `CAMPFIRE_SCHEMA` (default `main.default`).
- The digest job runs as the pipeline SP: same schema grants + CAN_QUERY on
  the serving endpoint.

### One-time setup, ADO

1. Branch policies on `dev`/`stg`/`prod`: require 1 reviewer (the human gate).
2. Pipelines → New pipeline → Azure Repos Git → this repo → existing YAML →
   `/azure-pipelines.yml`.
3. Pipeline Variables: `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`,
   `DATABRICKS_CLIENT_SECRET` (mark secret ✓).

### One-time setup, GitHub (only while code is authored on GitHub)

Repository secrets: `ADO_REPO_URL` = `https://dev.azure.com/<org>/<project>/_git/<repo>`,
`ADO_PAT` = Code (Read & Write) PAT.

### Going ADO-standalone (corporate)

Delete `.github/` and push this repo directly into corporate ADO Repos.
Everything else — pipeline, bundle, app — is already ADO-native and unchanged.

## Security

Tokens live ONLY in GitHub Actions secrets (mirror PAT), ADO pipeline
variables (Databricks SP), and Databricks secret scopes (app config). Never in
code, YAML values, or git history. The pipeline's service principal owns all
deployed resources — never deploy manually to the same target.
