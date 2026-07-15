# Campfire — Corporate Environment Migration Checklist

Everything needed to stand Campfire up in a corporate Azure DevOps org +
Databricks workspace, in order. The app and pipeline are fully portable —
the only bindings to any environment are the values called out below.
(Genie space setup has its own doc: `genie-instructions.md`.)

## 1. Import the repo (ADO-standalone)

1. Import this repo into the corporate ADO project (Repos → Import), or push
   a clone. **Delete `.github/`** — the GitHub mirror was only for the
   GitHub-authored phase; corp is ADO-native and nothing else changes.
2. Branch layout: `dev` is the default/protected branch; work lands via PRs
   from feature branches. Add branch policy on `dev` (and later `stg`/`prod`):
   minimum 1 reviewer. Note for solo bootstrap: enable "requestors can
   approve their own changes" or you can't merge your own PRs.

## 2. Edit the environment-bound values (one commit)

These are the ONLY places the repo mentions a specific environment. Set them
once for corp:

| file | key | set to |
|---|---|---|
| `src/app.yaml` | `SERVING_ENDPOINT` | corp Claude/Sonnet serving endpoint name |
| `src/app.yaml` | `CAMPFIRE_SCHEMA` | governed catalog.schema (corp rarely allows `main.default`) |
| `src/app.yaml` | `ADO_ORG_URL` / `ADO_PROJECT` | corp ADO org URL + project (feeds Now Shipping) |
| `databricks.yml` | `campfire_digest_job` → `parameters` | same schema + same endpoint as above (the job does not read app.yaml!) |

Keep the two files identical across dev/stg/prod branches — per-workspace
differences live only in secret VALUES (next step).

## 3. Per-workspace secrets (repeat for each environment's workspace)

Run as YOUR user (or a workspace admin), against each workspace:

```bash
databricks secrets create-scope campfire -p <profile>
databricks secrets put-secret campfire warehouse_id --string-value "<sql-warehouse-id>" -p <profile>
databricks secrets put-secret campfire ado_pat --string-value "<corp-ado-pat-code-read>" -p <profile>
# CRITICAL — the scope creator gets sole access; without this the deploy
# fails with "Secret ... does not exist (404)":
databricks secrets put-acl campfire <pipeline-sp-application-id> MANAGE -p <profile>
```

The `ado_pat` needs only **Code (Read)** — it powers the Now Shipping card.
Use a corp service-account PAT if policy requires.

## 4. Pipeline

1. Pipelines → New pipeline → Azure Repos Git → this repo → existing YAML →
   `/azure-pipelines.yml`. Create it through the UI wizard — definitions
   created via the REST API do not honor YAML CI triggers.
2. Edit → Variables: `DATABRICKS_HOST` (https://…), `DATABRICKS_CLIENT_ID`,
   `DATABRICKS_CLIENT_SECRET` (mark secret ✓) — the corp service principal.
   Multi-workspace: move to per-branch variable groups (`databricks-dev/-stg/-prod`).
3. Corp agent pools: if hosted parallelism is restricted, point `pool:` at a
   corp pool in `azure-pipelines.yml`.

## 5. First deploy

Merge a PR into `dev` (or Run pipeline on `dev`). The deploy creates: the
app, its service principal, and the `campfire-weekly-digest` job.

## 6. Post-deploy grants (once per workspace)

**App service principal** (shown on the app's page under Compute → Apps):

- Serving endpoint → Permissions → **Can query**
- SQL warehouse → Permissions → **Can use**
- SQL: `GRANT USE CATALOG ON CATALOG <catalog> TO \`<app-sp-id>\`;`
  `GRANT USE SCHEMA, CREATE TABLE, SELECT, MODIFY ON SCHEMA <catalog>.<schema> TO \`<app-sp-id>\`;`

**Digest job** runs as the pipeline SP: give it the same schema grants +
**Can query** on the serving endpoint.

Verification: `<app-url>/api/health` returns `{"status":"ok","ai":"live"}`;
save an entry; confirm `campfire_entries` exists; Now Shipping shows corp
repos (not the "sample data" badge).

## 7. Security

- PATs/SP secrets live only in: ADO pipeline variables, Databricks secret
  scopes. Never in the repo.
- Rotate any token that ever appeared in a chat/transcript before corp use.
- Agents push branches and open PRs; humans merge. The pipeline SP owns all
  deployed resources — never deploy manually to the same target.

## Known failure signatures (all hit during bring-up — save yourself the hour)

| symptom | cause → fix |
|---|---|
| deploy: `Secret with scope campfire ... does not exist (404)` | pipeline SP can't SEE the scope → step 3 `put-acl` |
| merge didn't trigger pipeline | definition was created via REST → recreate through the UI wizard (step 4.1) |
| deploy: `Organization ... has been cancelled or is not active` | workspace suspended (budget/trial) → fix account state, then just re-run |
| app deploys but `"ai": "fallback"` | missing Can-query on the endpoint → step 6 |
| `pushes join the open PR` / PR create 409s | one active PR per source→target in ADO — by design; retitle instead |
| entries vanish on app restart | warehouse secret/grants missing so app fell back to local store → steps 3 + 6 |
