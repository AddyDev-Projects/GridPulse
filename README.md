# GridPulse

**Agentic AI for the Cities of the Future.** GridPulse is a hackathon prototype of a multi-agent urban emergency-response **simulation**. Specialised agents share evidence and propose actions, a deterministic validator catches conflicts between them, the coordinator revises the plan, and a human operator approves the exact plan version before anything changes in the simulated city.

> **Simulation only.** Meridian Bay is fictional. All sensor readings are synthetic and seeded by scenario. GridPulse is not connected to any real traffic signal, power system, dispatch service, SCADA or sensor network.

## The 60-second story (Scenario A)

1. A resident reports flooding at the **Sector 4 Underpass**.
2. **Sentinel** structures the report (facts / inferences / unknowns) and retrieves synthetic water-level readings that corroborate it, including water entering the **S4 substation vault**.
3. **Mobility** closes the underpass and routes evacuees along **Evacuation Route R3** via Meridian Cross (NetworkX, 2,700 m).
4. **Grid/Utility** isolates **S4** because of the vault flooding.
5. The **validator** traces `S4 → T12 → Meridian Cross`: isolating S4 darkens signal T12, which controls an intersection on the evacuation route. Plan v1 is **blocked**.
6. The **coordinator** routes the finding back. Mobility reroutes via Ridgeway (3,700 m, +1,000 m); Grid's backup-feed alternative is recorded as viable but not selected. Plan **v2** is created and **revalidated**.
7. The operator approves **v2** (bound to its content hash and the city state version) and executes it. Only now does the persisted simulated city change.
8. Everything above is in the PostgreSQL audit trail.

Other scenarios: **B** stale/contradictory evidence (high-impact actions blocked, plan revised to verification-first), **C** no safe plan (no route and no energized backup feed, so the coordinator escalates and the city is untouched), **D** rejection (same data as A; reject and verify nothing changes).

## Architecture

```
frontend/  React 19 + TypeScript + Vite + Tailwind v4 + TanStack Query + React Router
backend/   FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL + NetworkX + Pydantic
  app/agents/         sentinel.py (LLM or rules), evidence.py, mobility.py, grid.py
  app/orchestration/  coordinator.py (state machine), revision.py (bounded remediation)
  app/validation/     validator.py (rules R1–R7), actions.py (action allowlist)
  app/simulation/     city.py, scenarios.py, state.py (graph logic), repository.py
  app/services/       decisions.py (approve / reject / revalidate / execute), plans.py, audit.py
  app/llm/            provider.py (fallback | openai_compatible | anthropic)
```

Responsibilities are split deliberately:

| Concern | Who decides |
|---|---|
| Interpreting free-text reports | Sentinel: LLM if configured, else transparent keyword rules; output schema-validated and location grounded against a gazetteer |
| Evidence freshness / corroboration | Deterministic code over synthetic observations |
| Routes | NetworkX weighted shortest path (Dijkstra) on the road graph |
| Power dependencies | NetworkX dependency graph (substation → signal → intersection) |
| Safety | `validate_plan()`. No LLM involved, and it can block anything |
| Revision | Deterministic remediation policy driven by validator findings, max `GRIDPULSE_MAX_REVISIONS` (default 2) |
| Authorization | Server-side: approval bound to plan id + version + SHA-256 content hash + city `state_version` |
| State change | `execute()` only, in one transaction, after a fresh pre-execution validation |

### Workflow states

`REPORTED → TRIAGED → EVIDENCE_GATHERED → PLANS_PROPOSED → VALIDATING → (REVISION_REQUIRED → VALIDATING_AGAIN)* → AWAITING_APPROVAL → APPROVED → EXECUTED`, with terminal `REJECTED`, `ESCALATED` (revision limit hit), `NO_SAFE_PLAN` (no remediation exists) and `FAILED`. Every transition is persisted in `workflow_transitions`. The workflow runs as a background task, and the UI polls it while it runs. Runs interrupted by a restart are marked `FAILED` on startup.

### Validator rules

| Rule | Checks |
|---|---|
| R1 Road safety | Route continuity; no closed roads, no roads the plan closes, no roads with a reported hazard |
| R2 Infrastructure dependency | Projects utility actions onto the dependency graph; blocks routes through intersections whose signals go dark; reports downstream effects |
| R3 Action validity | Allowlisted action types, correct target kinds, strict parameter schemas, valid power transfers |
| R4 Approval required | Reports high-impact actions; execution is impossible without an approval of this exact version |
| R5 Stale state | Plan / approval checked against a different `state_version` must be revalidated and re-approved |
| R6 Evidence | High-impact actions require CORROBORATED evidence; stale / degraded sensors raise warnings |
| R7 Unresolved conflict | Added by the coordinator when revision is impossible or the limit is reached |

### Safeguards worth knowing

- `UNIQUE(simulation_executions.plan_id)` plus a `SELECT … FOR UPDATE` on the simulation row makes double execution impossible (tested with 4 concurrent requests).
- A partial unique index allows at most one active approval per plan.
- Any reset or execution bumps `state_version`, so earlier validations and approvals go **stale automatically**. Execute then returns 409 `stale_approval` and the UI offers *Revalidate*.
- Execution failure rolls back every change (tested by injecting a failure mid-write).
- Model failures fall back to rules and are recorded (`fallback_used`, audit `llm.fallback`). The validator still runs.

## Setup

GridPulse needs **PostgreSQL**; there is no SQLite or in-memory fallback. The backend refuses to start without a valid PostgreSQL URL.

| Prerequisite | Version | Notes |
|---|---|---|
| Python | 3.12+ | Windows: python.org installer, tick "Add python.exe to PATH" (the `py` launcher is used if present) |
| Node.js | 20.19+ or 22.12+ | Required by Vite 8 (declared in `frontend/package.json` `engines`) |
| PostgreSQL | 16 | Docker Desktop, the Windows installer, Homebrew or a Linux package |
| Git | any | |

The setup scripts do everything after PostgreSQL is running:

| Step | Windows PowerShell | macOS / Linux |
|---|---|---|
| One-time setup | `scripts\setup.ps1` | `scripts/setup.sh` |
| Start backend (applies migrations first) | `scripts\start-backend.ps1` | `scripts/start-backend.sh` |
| Start frontend | `cd frontend; npm run dev` | `cd frontend && npm run dev` |

Each setup script, in order:
1. Creates `.env` from `.env.example` if it is missing, then stops so you can fill it in.
2. Creates `backend/.venv` and installs `backend/requirements.txt`.
3. Runs `backend/scripts/db_setup.py`, which creates the role and the two databases named in `.env`. It never drops, resets or re-passwords anything.
4. Runs `alembic upgrade head` on the development database.
5. Runs `npm ci` in `frontend/`.

Re-running a setup script is safe.

### Configuration (`.env`)

```bash
cp .env.example .env
```

On Windows: `Copy-Item .env.example .env`. `.env` lives in the repository root and is git-ignored. Fill in every `CHANGE_ME`:

| Variable | Required | Purpose |
|---|---|---|
| `GRIDPULSE_DATABASE_URL` | yes | Development database, e.g. `postgresql+psycopg://gridpulse:<password>@localhost:5432/gridpulse`. You choose the password; setup creates the role with it |
| `GRIDPULSE_TEST_DATABASE_URL` | for tests | Must be a different database whose name ends in `_test`. The test suite wipes it |
| `GRIDPULSE_PG_ADMIN_USER` / `GRIDPULSE_PG_ADMIN_PASSWORD` | for setup | PostgreSQL superuser used only by `db_setup.py` (and as the Docker container's password). The running app never uses them |
| `GRIDPULSE_LLM_*`, `GRIDPULSE_WORKFLOW_STEP_DELAY_MS`, ... | no | See comments in `.env.example` |

If a password contains `@ : / ? # %`, URL-encode it in the URLs (`@` becomes `%40`).

### Windows (PowerShell)

PowerShell blocks unsigned scripts by default, so the commands below pass `-ExecutionPolicy Bypass` for that one invocation. Run everything from the repository root.

**Option A: PostgreSQL in Docker Desktop.** Set `GRIDPULSE_PG_ADMIN_USER=postgres` and any `GRIDPULSE_PG_ADMIN_PASSWORD` in `.env`, then:

```powershell
docker compose up -d postgres
```

Wait until `docker compose ps` shows `healthy`.

**Option B: native PostgreSQL 16.** Install it; the installer asks for a password for the `postgres` superuser. Put that password in `GRIDPULSE_PG_ADMIN_PASSWORD`.

```powershell
winget install --id PostgreSQL.PostgreSQL.16 -e
```

Make sure the service is running (the name can differ; list them with `Get-Service postgresql*`):

```powershell
Start-Service postgresql-x64-16
```

**Then, for either option:**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
```

In a second PowerShell window:

```powershell
cd frontend; npm run dev
```

### macOS / Linux

**Option A: Docker.** Set the admin variables in `.env` as for Windows, then:

```bash
docker compose up -d postgres
```

**Option B: Homebrew (macOS).** The superuser is your macOS account with no password. Set `GRIDPULSE_PG_ADMIN_USER=<your macOS username>` and leave `GRIDPULSE_PG_ADMIN_PASSWORD` empty.

```bash
brew install postgresql@16
```

```bash
brew services start postgresql@16
```

**Then:**

```bash
./scripts/setup.sh
```

```bash
./scripts/start-backend.sh
```

```bash
cd frontend && npm run dev
```

### Manual steps (without the scripts)

From `backend/`, using the virtual environment's Python (`.venv\Scripts\python.exe` on Windows, `.venv/bin/python` elsewhere):

1. `python -m venv .venv` (with Python 3.12+)
2. `<venv python> -m pip install -r requirements.txt`. `requirements.lock` holds the full pinned set the tests passed with; it carries platform markers, so it also installs on Windows.
3. `<venv python> scripts/db_setup.py`. Add `--check` to test connections without changing anything.
4. `<venv python> -m alembic upgrade head` **before** the first start. The backend does not create tables itself.
5. `<venv python> -m uvicorn app.main:app --host 127.0.0.1 --port 8000`

Then, in `frontend/`: `npm ci`, then `npm run dev`.

On first start the backend seeds the fictional city with Scenario A, but only if the database is empty. Restarts keep all data.

### Health checks

| Check | Expected |
|---|---|
| http://127.0.0.1:8000/api/health | `{"status":"ok", ...}`: the API process is up |
| http://127.0.0.1:8000/api/ready | `"database":"ok"`, `"migration":"0001"`: connected and migrated |
| `backend/scripts/db_setup.py --check` | `OK` for the development and test databases |
| http://localhost:5173 | Landing page; the console's header shows the scenario and city state version |

PowerShell: `Invoke-RestMethod http://127.0.0.1:8000/api/ready`. macOS/Linux: `curl http://127.0.0.1:8000/api/ready`.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ConfigurationError: GRIDPULSE_DATABASE_URL is not set` | No `.env` in the repository root, or the variable is missing. Copy `.env.example` to `.env` |
| `... still uses the placeholder from .env.example` | Replace every `CHANGE_ME` in `.env` |
| `... must be a PostgreSQL URL` | GridPulse only supports PostgreSQL. Use `postgresql+psycopg://...` |
| `connection refused` / `Is PostgreSQL running?` | Start it: `docker compose up -d postgres`, `Start-Service postgresql-x64-16` or `brew services start postgresql@16`. Check that port 5432 isn't used by another server |
| `password authentication failed for user "gridpulse"` | The role already existed with a different password. `db_setup.py` never changes passwords; update the URL in `.env`, or run `ALTER ROLE gridpulse PASSWORD '...'` as a superuser |
| `password authentication failed for user "postgres"` during setup | Wrong `GRIDPULSE_PG_ADMIN_PASSWORD`. On Homebrew, set `GRIDPULSE_PG_ADMIN_USER` to your macOS username instead |
| `relation "simulation_meta" does not exist` | Migrations weren't applied. Run `alembic upgrade head`; the start-backend scripts do this automatically |
| `running scripts is disabled on this system` (Windows) | Use `powershell -ExecutionPolicy Bypass -File scripts\...` as shown |
| `py` or `python` not found / wrong version (Windows) | Install Python 3.12+ from python.org with "Add python.exe to PATH"; check with `py -3.12 --version` |
| Frontend shows "Cannot reach the GridPulse API" | Backend not running on 127.0.0.1:8000. Start it, or set `GRIDPULSE_API_URL` before `npm run dev` |
| `npm ci` fails with an engine or Vite error | Node is older than 20.19. Install Node 22 LTS |
| Docker: `Set GRIDPULSE_PG_ADMIN_PASSWORD in .env` | Compose reads that variable from the root `.env` |


## Running the demo

1. Landing page → **Launch Simulation**.
2. Pick a scenario card → **Load scenario** (resets the fictional city).
3. **Submit and run agents** with the prefilled synthetic report. The workflow stepper animates as the coordinator progresses (`GRIDPULSE_WORKFLOW_STEP_DELAY_MS` controls pacing).
4. **Plans and validation**: the conflict on the graph, findings, before/after diff, alternatives, and the uncoordinated-vs-GridPulse comparison.
5. **Approval**: enter an operator name, approve v2, then execute. Or reject (Scenario D).
6. **City graph** and **Audit trail** show the persisted result.

To demonstrate a stale approval: approve a plan, then reset the city from the dashboard, then try to execute. The backend refuses and the plan becomes STALE.

## LLM provider

The hackathon "Astra" credits could not be inspected from this machine: no credentials or provider documentation were available, and a web search suggests GPT-6 Astra (OpenAI). GridPulse therefore ships with a provider abstraction and runs in **fallback mode by default**:

| `GRIDPULSE_LLM_PROVIDER` | Behaviour |
|---|---|
| `fallback` (default) | Rule-based Sentinel, no network. Clearly labelled "Rule-based fallback" in the UI |
| `openai_compatible` | Any Chat Completions endpoint. Set `GRIDPULSE_LLM_BASE_URL`, `GRIDPULSE_LLM_API_KEY`, `GRIDPULSE_LLM_MODEL` (use this for Astra if it exposes such an API) |
| `anthropic` | Claude via the official Anthropic SDK with structured outputs. Set `GRIDPULSE_LLM_API_KEY`; model defaults to `claude-opus-5-5`; server-side refusal fallbacks enabled |

Only Sentinel's triage uses a model. Outputs are Pydantic-validated, model-proposed locations must exist in the gazetteer, calls have a timeout and bounded retries, and any failure falls back to rules. Keys stay server-side.

## Tests

Tests use **only** the database in `GRIDPULSE_TEST_DATABASE_URL`. The suite migrates it down and up and truncates every table. It refuses to start if that URL is missing, if the database name does not end in `_test`, or if it points at the same database as `GRIDPULSE_DATABASE_URL`. Development data is never touched. Create the test database with the setup script, or with `scripts/db_setup.py`.

Windows (from `backend\`):

```powershell
.\.venv\Scripts\python.exe -m pytest
```

macOS / Linux (from `backend/`):

```bash
.venv/bin/python -m pytest
```

59 backend tests. They cover:
- **Core logic:** routing, dependency traversal, evidence classification, every validator rule, and real revision (reroute, backup power, verification-first, no-safe-plan).
- **Agents:** Sentinel fallback and grounding.
- **End to end:** the full Scenario A flow.
- **Safeguards:** rejection, unapproved/invalid execution, version/hash binding, stale approval and revalidation, escalation, revision limit, provider failure, duplicate and concurrent execution, rollback on failure, and persistence across an app restart.
- **API and schema:** API contracts, migration/model parity, and a downgrade/upgrade round trip.
- **Configuration:** a missing, placeholder or non-PostgreSQL URL fails clearly without echoing secrets, and the test database cannot be the development database.

Migration drift check (from `backend/`, venv Python): `python -m alembic check`.

Frontend (same commands on all platforms, from `frontend/`):

```bash
npm run typecheck
```

```bash
npm run lint
```

```bash
npm test
```

```bash
npm run build
```

## Known limitations

- Hackathon prototype, not production. No authentication: the operator name is self-declared.
- Any city-state change invalidates outstanding approvals (deliberately conservative), even if it doesn't touch the plan.
- Pre-execution validation reuses the evidence captured during the workflow rather than re-querying sensors.
- Revision strategies are deterministic and specific to the action types in the allowlist; Mobility and Grid do not use an LLM.
- The live LLM paths (`openai_compatible`, `anthropic`) are implemented but were not exercised against a real endpoint (no credentials). Fallback mode is what was tested.
- `prefers-reduced-motion` behaviour is covered by component tests; it was not emulated in a real browser.
- **Not verified on Windows.** Everything was built and tested on macOS with Homebrew PostgreSQL 16. The Windows side was checked as far as possible from macOS: a Windows dependency resolution, a download of every Windows wheel for Python 3.12, a check that the npm lockfile includes the Windows native bindings, and ASCII-only PowerShell scripts. `setup.ps1` and `start-backend.ps1` have not been run on Windows.
- Docker Compose was not run (Docker was not installed on the build machine).
