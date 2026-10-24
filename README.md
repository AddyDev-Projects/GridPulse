# GridPulse

**Agentic AI for the Cities of the Future, on the real map of Mangaluru.**

GridPulse is a hackathon prototype of a disaster-response coordination system for Mangaluru, Karnataka:
1. Citizens report hazards anonymously.
2. Specialised agents assess each report and plan a response on the city's real OpenStreetMap road network.
3. A deterministic validator catches conflicts between the agents' proposals.
4. An authenticated operator approves or rejects the exact plan version.
5. Only then does the *simulated* city state change.

> **Simulation.** Roads, junctions, traffic-signal locations and emergency facilities are real OpenStreetMap data.
> Sensors, the power network, road closures and all "executions" are simulated and labelled as such. GridPulse does
> not dispatch real services and has no live sensor or traffic feed. In an emergency, call 112.

## What is real, configured, simulated, or missing

| Capability | Status |
|---|---|
| Mangaluru road graph (7,348 junctions, 9,151 segments, one-way aware) from OSM | **Implemented and tested** (`backend/data/mangaluru`, ODbL) |
| Routing that avoids closures and reports "no route" | **Implemented and tested** |
| Hospitals, fire station, police stations, traffic-signal locations | **Real OSM data**. Coverage is limited: one fire station and 5 signals are mapped in the area |
| Anonymous citizen reporting with rate limits and abuse controls | **Implemented and tested** |
| Operator accounts (Argon2id), sessions, CSRF, role-based access control, provisioning CLI | **Implemented and tested** |
| Agent workflow, validator, revision, versioned approval, transactional execution, append-only audit | **Implemented and tested** |
| Sentinel LLM triage (Gemini via OpenAI-compatible endpoint, or Claude) | **Requires configuration**. Rule-based fallback otherwise. Live Gemini call verified earlier with `gemini-3.5-flash-lite` |
| Evidence-grounded responder recommendations and medical facility assessment | **Implemented**. Recommendations are labelled as reported/inferred; facility capability and availability remain unknown unless verified |
| Map presentation | **Google Maps JavaScript API when configured** (with a **Show traffic** toggle for Google's visual layer), with a single-attempt fallback to Leaflet + OpenStreetMap. The OpenStreetMap provider has an optional **3D** view (MapLibre GL, OpenFreeMap vector tiles, extruded OSM buildings). Routing remains the local OSM/NetworkX graph |
| Basemap tiles (tile.openstreetmap.org) | **External service** under the OSM tile usage policy (light use, attribution). Needs internet; nothing else depends on it. Use your own or a commercial tile provider for production |
| Sensors and sensor readings | **Simulated**. They exist only while an explicitly loaded demo scenario is active |
| Power network (feeder zones and signal power) | **Simulated, illustrative**. No utility data is available |
| Road closures, routes and "execution" of plans | **Simulated**. Recorded in the database only |
| Live traffic, live sensors, real dispatch, SMS or notifications | **Not available to the backend workflow**. The optional Google `TrafficLayer` is visual-only and is never treated as evidence or routing authority |

## Two experiences

| | Citizen (public) | Operator (authenticated) |
|---|---|---|
| Entry | `/report`, `/status` | `/operator/login`, then `/ops` |
| Account | None. No name or phone number is asked for | Created by an administrator with the CLI. There is no self sign-up |
| Can do | Pin a hazard on the map, describe it, select fire/medical/accident/rescue or police categories, get a reference (e.g. `GP-7K2M9QXA`), track its status, view the five newest sanitized reports, confirmed incidents and simulated closures | Review the queue; confirm or dismiss reports; run the agents; inspect evidence, responder and hospital recommendations, traffic analysis, routes, dependencies, findings and plan versions; approve, reject or execute; view the audit trail |
| Sees | Coarse status labels; incident locations rounded to about 100 m; closures | Full report, assessment, evidence, agent outputs, plans, audit |
| Cannot | Call any operator endpoint. The backend returns 401 regardless of what the browser sends | Act as admin unless their role is admin (demo scenarios need admin) |

Every permission is enforced in the backend (`app/api/deps.py`). The frontend only hides controls for convenience.

## Architecture

```
frontend/  React 19 + TypeScript + Vite + Tailwind v4 + TanStack Query + React Router + Google Maps JS / Leaflet fallback
  features/public     citizen report form, status lookup, public safety map
  features/auth       operator sign-in
  features/dashboard  report queue, overview map, demo-scenario controls (admin)
  features/incidents  overview, plans and validation, approval, audit
  features/map        provider abstraction: Google Maps JS primary (traffic toggle), Leaflet/OSM fallback with optional
                      MapLibre 3D (OSM only); one palette (mapTheme.ts); backend routes and graph overlay
backend/   FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL + NetworkX + Pydantic + argon2-cffi
  app/geo             osm.py (Overpass build of the graph), road_graph.py (load, route, spatial index)
  app/agents          sentinel.py, evidence.py, geo_context.py, mobility.py, traffic.py, grid.py
  app/orchestration   coordinator.py (state machine), revision.py
  app/validation      validator.py (R1–R8), actions.py (allowlist)
  app/simulation      state.py (snapshot), infrastructure.py (simulated overlay), scenarios.py, repository.py
  app/services        auth.py, reports.py, decisions.py, ratelimit.py, plans.py, audit.py
  app/api             public.py (anonymous), auth.py, routes.py (operator), deps.py (authorization)
  app/cli.py          operator provisioning, geodata, demo scenarios
```

### The city and its graph

`Mangaluru` is a row in `cities`: name, state, country, centre and coverage box. Each loaded OSM extract is a row in
`road_graph_versions`, with its SHA-256, OSM timestamp and counts. Exactly one version is active.

The road graph is the city's street network:
- **Nodes** are road junctions (OSM node ids such as `n288094800`).
- **Edges** are road segments between junctions (ids such as `w164559538s0`), with geometry, one-way direction and an estimated travel time.

The city is not one node of the graph. Incidents, closures and graph versions all reference the city. The graph file
is loaded into memory once at startup and verified against its checksum. Dynamic state is kept in PostgreSQL:
closures and caution segments in `road_closures`, and the simulated overlay in `infrastructure_nodes/edges`
(dataset `mangaluru_sim`).

### Workflow

`REPORTED → TRIAGED → EVIDENCE_GATHERED → PLANS_PROPOSED → VALIDATING → (REVISION_REQUIRED → VALIDATING_AGAIN)* → AWAITING_APPROVAL → APPROVED → EXECUTED → RESOLVED`,
with terminal states `DISMISSED`, `REJECTED`, `ESCALATED`, `NO_SAFE_PLAN`, `FAILED` and `RESOLVED`.
- **Resolve emergency** (operator, after confirmation) closes an incident: it leaves the active queue, operator maps and
  the public map; its report, plans and audit trail are kept (`GET /api/incidents?state=resolved`). Unexecuted plans are
  withdrawn (`INVALID`) and their approvals invalidated, so nothing executes as a side effect. The incident's response
  routes stop being active. Simulated road closures and power state are city state and stay until lifted separately.
  Resolving twice is a no-op (same record, no second transition or audit event).
- Report time is set by the server when the report is accepted (UTC, `timestamptz`; database sessions run in UTC so the
  API always returns UTC); the form has no time field and the API rejects a client-supplied time. The UI shows local time.
- The public latest-five feed (`/api/public/recent-incidents`) excludes resolved and dismissed reports in the query,
  before ordering (`created_at DESC, id DESC`) and the limit, so older active reports fill the list.
- City-state version numbers stay internal (approval binding, stale-plan detection, audit details). The UI says what
  they mean, e.g. "The city state has changed since this plan was generated. Revalidate the plan before approving or
  executing it."

### Citizen report form

- **Location:** on load the page asks the browser once for the current position (no tracking). If granted and inside the
  mapped area it becomes the default pin ("Using your detected location"). **Pick location on map** enables map
  clicks; a picked location always wins over a late geolocation answer, and **Use detected location** returns to it.
  Denied, failed, unsupported or out-of-area positions fall back to picking. The position is only sent with the report.
- **Speak instead:** uses the browser's own speech recognition (Web Speech API; no key, no paid service). Recognised
  text is appended to the editable description; nothing is recorded or stored by GridPulse and nothing is submitted
  automatically. Where the browser has no speech recognition, a note replaces the button. Note: some browsers (e.g.
  Chrome) send the audio to their vendor's speech service to transcribe it.
- Operators start the workflow by hand. Nothing runs on submission.
- Every transition is persisted.
- A run interrupted by a restart is marked `FAILED`.

### Agents

| Agent | Purpose | Inputs → outputs | How it works | Failure modes | Tests |
|---|---|---|---|---|---|
| **Sentinel** (triage) | Structure the citizen's text | Report text, chosen category, indicators, nearest road → `SentinelAssessment` (facts, inferences, unknowns, urgency, confidence) | LLM with a strict schema, or transparent keyword rules. Location always comes from the map pin; a model-proposed location is discarded | Provider error or timeout, or malformed or refused output → rule-based fallback, recorded (`fallback_used`, audit `llm.fallback`) | `test_agents.py`, `test_llm_provider.py` |
| **Sentinel** (evidence) | Decide how well the hazard is supported | IncidentGeo, synthetic sensor rows, operator confirmation → `EvidenceAssessment` | Deterministic: freshness window, thresholds, contradictions → CORROBORATED / OPERATOR_CONFIRMED / CONTRADICTORY / STALE / UNCORROBORATED | No sensors → UNCORROBORATED. Never invents readings | `test_agents.py` |
| **Department selection** (Coordinator) | Decide which departments the report justifies, and why | Citizen's category, ticked indicators, Sentinel's hints → fire & rescue / ambulance / police, each `required`, `recommended`, `conditional` or `not_justified`, with basis (`reported`, `inference` or `rule`), reason, evidence, unknowns and expected action | Deterministic rule table (`app/agents/services.py`). Examples: accident → police required (scene-safety rule), ambulance conditional until an injury is reported, fire & rescue only with entrapment, fire or structural damage; flood → fire & rescue required only if people or vehicles are stranded, otherwise conditional, police recommended for traffic management. Text mentions can only make a department conditional. Only `required` departments get a route | A facility nearby never creates a requirement; an unclear report gets a verification-only plan | `test_departments.py`, `test_services_traffic.py` |
| **Sentinel** (resources) | Infer likely responder types and treatment need | Report text, selected category/indicators → responder recommendations, affected-people and hazard statements | Deterministic evidence-grounded classification; police/crime details remain allegations and recommendations never dispatch | Ambiguous reports stay uncertain and are shown to operators for verification | `test_agents.py`, `test_workflow.py` |
| **Geo context** | Put the report on the graph | lat/lon, category, indicators → nearest segment and node, hazard radius, segments in the hazard area, simulated feeder zone | Spatial index and point-to-polyline distance. Radius is a stated per-category assumption (flood 150 m, ×1.3 if water is rising) | Outside coverage or no road within 400 m → no road actions, escalated | `test_agents.py` |
| **Mobility** | Close roads and plan routes for required services only | Snapshot, assessment, evidence, geo, services → `close_roads` and one `designate_response_route` per required service, with real edge paths | Dijkstra by estimated travel time on the one-way-aware graph: fire & rescue from the nearest reachable OSM fire station (`emergency_access`), police from the nearest reachable OSM police station (`police_response`), ambulance from the staging point to the top-ranked reachable OSM hospital (`medical_transfer`, the incident-to-hospital leg). The ambulance's leg *to* the incident is reported as unavailable: OpenStreetMap has no ambulance stations in the area and a hospital is not assumed to be one. Every leg is recorded (`route_legs`), planned or not. No cross-service fallback | A required fire or police route with no path → NO_SAFE_PLAN; a missing medical leg is an R9 warning | `test_geo.py`, `test_agents.py`, `test_services_traffic.py` |
| **AnalyseTraffic** | Assess each plan version's routes and adjust the draft plan | The assembled plan, snapshot, evidence → route status (clear / caution / blocked) against the state the plan itself produces, meaningfully different alternatives, recommendations; stamped with plan and city-state version | Runs on every plan version (initial and each revision). Changes the plan where justified: reroutes a route off caution segments if the detour is small (≤25 % or ≤4 min), adds a diversion notice when the plan closes roads (never for crime, violence or medical reports). Revalidation and pre-execution re-analyse against the current state. Live traffic is explicitly unavailable | A failure is recorded and validation continues with an R8 warning; unsafe routes stay R1/R2-blocked | `test_services_traffic.py`, `test_resolution.py` |
| **Grid / Utility** | Act on the simulated power model | Evidence, overlay → `isolate_substation` (feeder zone) | Graph traversal: feeder zone → signals that lose their only active feed → junctions they control | Isolates only on corroborated or operator-confirmed zone evidence | `test_agents.py` |
| **Coordinator** | Orchestrate | All of the above | Explicit state machine; persists runs, proposals, plan versions, validation; bounded revision (`GRIDPULSE_MAX_REVISIONS`, default 2) | Any exception → FAILED with no state change; revision impossible → NO_SAFE_PLAN; limit reached → ESCALATED | `test_workflow.py` |
| **Validator** | Independent safety authority | Actions, snapshot, evidence | Rules R1–R7 below. No LLM. Runs at plan time, on revalidation and immediately before execution | Blocking finding → the plan cannot be approved or executed | `test_agents.py`, `test_workflow.py` |

### Validator rules

| Rule | Checks |
|---|---|
| R1 Road safety | Route is a valid, direction-aware traversal of the real graph; no closed segments, none closed by the same plan, none inside the reported hazard area |
| R2 Infrastructure dependency | Projects utility actions onto the dependency graph; blocks routes through junctions whose (real OSM) signal would lose its (simulated) power |
| R3 Action validity | Allowlisted action types, existing targets of the right kind, strict parameter schemas, valid feed transfers; one route per purpose; a route's service must match its purpose |
| R4 Approval required | Reports high-impact actions; execution requires an operator approval of this exact version |
| R5 Stale state | A plan validated or approved against an older city state must be revalidated and re-approved |
| R6 Evidence | High-impact actions need sensor-corroborated or operator-confirmed evidence. Otherwise the plan is revised to verification-first (caution, field check, advisory) |
| R7 Unresolved | Added when no remediation exists or the revision limit is reached |
| R8 Traffic data | Warnings only (unsafe routes are R1/R2): live traffic unavailable; traffic analysis failed; analysis made for another plan or city-state version; info when a route has no alternative |
| R9 Departments | Blocking: a route for a department the incident does not require. Warnings: a required department without a recorded reason, or with no valid road route. Info: ambulance starting point unknown; route withheld pending verification |

### Safeguards

- **Approval binding:** an approval records the operator's account, the plan version, the content hash and the city state version. A mismatch or a later state change voids it.
- **No duplicate execution:** `UNIQUE(simulation_executions.plan_id)` plus a row lock serialise execution. Tested with 4 concurrent requests: exactly one succeeds.
- **Atomic execution:** a failed execution rolls back completely. Audit events are append-only, enforced by a PostgreSQL trigger.
- **Sessions:** only SHA-256 hashes of session and CSRF tokens are stored. Cookies are HttpOnly and SameSite=Strict, with Secure configurable. Sessions expire after 12 h, or 2 h idle.
- **Brute force:** 5 failed logins per username per 15 min, with an identical error for unknown users and wrong passwords.
- **Abuse controls:** 5 reports per 10 min and 20 per day per client (keyed by HMAC; raw IPs are not stored), a honeypot field, 64 KB request bodies, and a coverage-area check.

## Data sources and external services

| Service | Used for | Required? | Credentials | When unavailable |
|---|---|---|---|---|
| OpenStreetMap via the Overpass API | Building the road-graph file (`python -m app.cli geodata fetch`) | Only to rebuild | None (sends a User-Agent) | Use the committed graph file; nothing at runtime calls Overpass |
| OpenStreetMap tile server | 2D map background in the browser | Optional | None (attribution shown on the map) | Grey map background; routes, closures and the road-graph overlay still draw from the backend |
| OpenFreeMap (`tiles.openfreemap.org`, OpenMapTiles vector tiles from OSM) | Optional 3D view (OSM provider only) | Optional | None | The 3D view reports the error and returns to 2D |
| Google Maps JavaScript API | Optional primary browser map and optional visual `TrafficLayer` | Optional | `VITE_GOOGLE_MAPS_API_KEY` in the root `.env` | One failed load/auth attempt switches to Leaflet + OSM; no retry loop. Google traffic is not sent to or trusted by the backend workflow |
| Gemini (OpenAI-compatible endpoint) or Anthropic | Sentinel triage text | Optional | `GRIDPULSE_LLM_*` in `.env` | Rule-based triage, clearly labelled; the validator is unaffected |
| PostgreSQL 16 | All persistent state | Yes | `GRIDPULSE_DATABASE_URL` | Backend refuses to start |

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
5. Generates `GRIDPULSE_SECRET_KEY` in `.env` if it still says `CHANGE_ME` (`backend/scripts/ensure_secret.py`).
6. Runs `npm ci` in `frontend/`.

Re-running a setup script is safe. After the first setup, create the first administrator (see *Operator accounts*).

### Configuration (`.env`)

```bash
cp .env.example .env
```

On Windows: `Copy-Item .env.example .env`. `.env` lives in the repository root and is git-ignored. Fill in every `CHANGE_ME`:

| Variable | Required | Purpose |
|---|---|---|
| `GRIDPULSE_DATABASE_URL` | yes | Development database, e.g. `postgresql+psycopg://gridpulse:<password>@localhost:5432/gridpulse`. You choose the password; setup creates the role with it |
| `GRIDPULSE_TEST_DATABASE_URL` | for tests | Must be a different database whose name ends in `_test`. The test suite wipes it |
| `GRIDPULSE_SECRET_KEY` | yes | ≥ 32 random characters; keys the HMAC of client addresses used for rate limiting. Setup generates it |
| `GRIDPULSE_COOKIE_SECURE` | yes for production | `true` (default) sends session cookies over HTTPS only. Set `false` only for local http development |
| `GRIDPULSE_PG_ADMIN_USER` / `GRIDPULSE_PG_ADMIN_PASSWORD` | for setup | PostgreSQL superuser used only by `db_setup.py` (and as the Docker container's password). The running app never uses them |
| `GRIDPULSE_LLM_*`, `GRIDPULSE_WORKFLOW_STEP_DELAY_MS`, ... | no | See comments in `.env.example` |
| `VITE_GOOGLE_MAPS_API_KEY` | no | Browser-restricted Google Maps JavaScript API key. Leave empty for local Leaflet/OSM development |
| `VITE_GOOGLE_MAPS_TRAFFIC_LAYER` | no | Set `true` only when the Google Maps JavaScript `TrafficLayer` is enabled and authorized. This affects presentation only |

If a password contains `@ : / ? # %`, URL-encode it in the URLs (`@` becomes `%40`).

### Optional Google Maps setup

The app remains fully usable without Google Maps. To enable the primary provider:

1. In a Google Cloud project, enable **Maps JavaScript API** and configure billing as required by Google Maps Platform.
2. Create a browser key and restrict it by HTTP referrer (for local development, the `localhost:5173` and
   `127.0.0.1:5173` origins) and by the APIs it may call. Do not put a server credential in the frontend variable.
3. Set `VITE_GOOGLE_MAPS_API_KEY` in the root `.env`. Optionally set `VITE_GOOGLE_MAPS_TRAFFIC_LAYER=true` to request
   Google's supported visual traffic layer.
4. Restart Vite. The map shows a small provider indicator. Missing keys, authorization/billing failures, network errors,
   and map-load failures fall back to Leaflet + OpenStreetMap without retrying the failed Google request.

Google Maps is a visual provider only in GridPulse. AnalyseTraffic continues to label live traffic as unavailable and
uses the versioned OSM graph plus simulated closure/caution state. It never scrapes Google Maps or treats a basemap as
traffic evidence. Review the current Google Maps Platform documentation and terms for pricing, quotas, attribution,
key restrictions, and allowed uses before production deployment: [Maps JavaScript setup](https://developers.google.com/maps/documentation/javascript/get-api-key),
[usage and billing](https://developers.google.com/maps/documentation/javascript/usage-and-billing),
[TrafficLayer](https://developers.google.com/maps/documentation/javascript/trafficlayer), and
[API key security guidance](https://developers.google.com/maps/api-security-best-practices).

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
| http://localhost:5173/report | Citizen report form with the Mangaluru map (no sign-in) |
| http://localhost:5173/ops | Redirects to operator sign-in until you log in |

PowerShell: `Invoke-RestMethod http://127.0.0.1:8000/api/ready`. macOS/Linux: `curl http://127.0.0.1:8000/api/ready`.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `GRIDPULSE_SECRET_KEY must be at least 32 random characters` | Run `backend/scripts/ensure_secret.py`, or the setup script |
| Sign-in works but every console request returns 401 over plain http | `GRIDPULSE_COOKIE_SECURE=true` makes the browser drop the cookie on http. Use `false` locally or serve over HTTPS |
| `Too many failed sign-in attempts` (429) | 5 failures per username per 15 min. Wait, or reset the password with the CLI |
| `GRIDPULSE_LLM_MODEL looks like a placeholder` | Set a real model id or `GRIDPULSE_LLM_PROVIDER=fallback` |
| Map tiles do not load | The basemap comes from tile.openstreetmap.org (needs internet). Routing and the road overlay still work offline |
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


## Operator accounts

Accounts are created only with the CLI, run from `backend/` with the virtual environment's Python:
- Windows: `.\.venv\Scripts\python.exe -m app.cli ...`
- macOS/Linux: `.venv/bin/python -m app.cli ...`

Passwords are typed twice at a hidden prompt, are never printed or logged, and are stored only as Argon2id hashes.
There is no default account.

| Task | Command |
|---|---|
| First administrator (once) | `python -m app.cli operator create <username> --role admin` |
| Another operator | `python -m app.cli operator create <username>` |
| Reset a password (revokes their sessions) | `python -m app.cli operator set-password <username>` |
| Disable / re-enable (disable revokes sessions) | `python -m app.cli operator disable <username>` / `enable <username>` |
| Change role | `python -m app.cli operator set-role <username> admin\|operator` |
| Sign someone out everywhere | `python -m app.cli operator revoke-sessions <username>` |
| List accounts | `python -m app.cli operator list` |

Rules:
- `create` refuses if the username exists. It never resets an existing account.
- Passwords must be at least 12 characters, must not contain the username, and must not be very repetitive or common.
- For automation, `--password-stdin` reads one line from stdin. Without it, the CLI refuses a non-interactive stdin.

Check that a citizen cannot reach operator functions (expect `401` from both commands):

```bash
curl -i -X POST http://127.0.0.1:8000/api/plans/00000000-0000-0000-0000-000000000000/execute
```

```bash
curl -i http://127.0.0.1:8000/api/incidents
```

## Demo walkthrough

1. **Citizen:** open http://localhost:5173/report, allow location (or **Pick location on map**), describe the hazard (type or **Speak instead**), tick indicators, then **Send report**. Note the reference.
2. **Operator:** sign in at http://localhost:5173/operator/login. The report is at the top of the queue.
3. Open it. The Overview shows the pin, the nearest real road and the reporter's indicators.
   - Click **Confirm hazard** if you have verified it (recorded with your name), then **Run agent workflow**.
   - Without confirmation and without sensors, the evidence is UNCORROBORATED, so the plan becomes verification-first (caution, field check, advisory). This is intended.
4. **Overview** shows which services are required (and why), facility candidates and the traffic analysis for the current plan version. **Plans and validation** shows the routes on the map (fire & rescue cyan, police violet, medical white, blocked magenta dashed), drawn from the exact segments the validator checked, plus findings, revisions and alternatives.
   - Try a police report (e.g. *Theft, robbery or burglary*): confirm, run, and the plan has one police-response route from an OSM police station and no fire route.
5. **Approval**: approve, then **Execute in simulation**. Or reject: nothing changes.
6. When the response is complete, **Resolve emergency** (with confirmation). The incident leaves the queue and the maps.
7. The citizen's `/status?ref=GP-…` now shows the result. Confirmed incidents and simulated closures appear on the public map.

### Demo scenarios (admin; simulated data)

Load from **Operations → Demo scenarios**, or with `python -m app.cli demo load <key>`. Each creates labelled synthetic
sensors at an illustrative location on real roads, plus a simulated report in the queue. `demo clear` lifts all
simulated closures; it deletes nothing.

| Key | Shows |
|---|---|
| `flood_conflict` (A) | The fire-station route crosses the real OSM signal on George Fernandes Road. The simulated isolation of feeder zone 2B would darken it. The validator blocks v1; Mobility reroutes on the graph (+0.6 km); v2 passes |
| `stale_evidence` (B) | A stale high reading plus a fresh dry reading gives CONTRADICTORY evidence, so the plan is revised to verification-first |
| `no_safe_plan` (C) | A simulated closure of Tannirbaavi Beach Road cuts off the area within the mapped box. No route exists, so the incident escalates. In reality the area also connects north, outside the box; this is a coverage limitation, documented as such |
| `approval_rejection` (D) | Same data as A; reject the plan and confirm nothing changes |

How the A and C locations were chosen: by searching the graph for points where the access route crosses a signal
that has a detour (A), and for points with a minimum edge cut of one segment between the hazard perimeter and every
responder base (C). Loading a scenario re-checks that it still applies to the current graph.

## LLM provider

| `GRIDPULSE_LLM_PROVIDER` | Behaviour |
|---|---|
| `fallback` (default) | Rule-based Sentinel; no network |
| `openai_compatible` | Any Chat Completions endpoint, e.g. Gemini at `https://generativelanguage.googleapis.com/v1beta/openai/` with a model such as `gemini-3.5-flash-lite` |
| `anthropic` | Claude via the Anthropic SDK (model defaults to `claude-opus-5-5`) |

The configuration is checked at startup: a missing key or model, or a placeholder model name, stops the backend with
a clear message. Requests have a timeout. Permanent 4xx errors are not retried. Provider error text is shown with
keys redacted. Output is schema-validated and the location is grounded to the map pin. The validator never consults
the model.

## Tests

The backend suite uses only `GRIDPULSE_TEST_DATABASE_URL`. That database must end in `_test` and must differ from
the dev database. The suite drops and recreates that database's schema at the start of each run; development data is never touched.

From `backend/`:

```bash
.venv/bin/python -m pytest
```

Windows: `.\.venv\Scripts\python.exe -m pytest`

164 backend tests, by area:

| File | Covers |
|---|---|
| `test_geo.py` | Graph checksum, connectivity, one-way, closures, no-route, spatial queries |
| `test_agents.py` | Evidence, Sentinel grounding, responder/resource inference, Mobility, AnalyseTraffic, Grid, every validator rule, revision paths |
| `test_auth.py` | Argon2id, login, throttling, CSRF, RBAC, anonymous denial, CLI provisioning |
| `test_public.py` | Anonymous reporting, police categories, five-report feed, validation, rate limit, size limit, public views leak nothing private |
| `test_workflow.py` | Citizen → operator → agents → approval → execution end to end; rejection; no-route; stale approval; concurrent execution; rollback; provider failure; legacy read-only; startup creates no data; append-only audit; migrations, including a refused downgrade |
| `test_departments.py` | Accident with and without entrapment, flood with and without stranded people, unclear reports, reasons and evidence on every required department, text mentions never required, R9 blocking a sneaked-in route, unroutable department reported, separate ambulance legs, departments and road-following geometry through the API |
| `test_services_traffic.py` | Service selection (police-only, medical-only, multi-service, text-only), no cross-service fallback, police report in a flooded zone, traffic: clear route, closed road, alternative, no alternative, route closed by its own plan, failed/stale analysis, caution reroute changing the plan, diversion notice, re-analysis after revision; duplicate/mismatched dispatch blocked |
| `test_resolution.py` | Police workflow end to end, verification-first without a public advisory, traffic per plan version, replanning after a state change, resolution (hides from queue/maps, keeps history, withdraws unexecuted plans, rejects duplicates/stale views/anonymous), server timestamps, migration 0005 |
| `test_config.py`, `test_llm_provider.py` | Configuration and provider behaviour |

LLM tests use a mocked HTTP layer; they do not call a live model.

Frontend, from `frontend/`:

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

The frontend tests cover:
- The action plan: why each department is involved (status, basis, evidence, unknowns, route legs, "Reason not provided — review required" when missing), actions in order with their responsible department and expandable details, hard failures vs warnings vs notes, absent fields, per-department route layers with start/end markers, and switching incidents without stale plans or routes.
- Citizen reporting without login, client-side validation, and the server's error shown on refusal.
- The public map, and the sign-in redirect on 401.
- Admin-only controls hidden from operators.
- CSRF header handling and open-redirect protection.
- A real Leaflet render, route casing, distinct police/fire station markers below routes and incidents.
- 2D/3D shown only for OpenStreetMap, starting in 2D and falling back to 2D when 3D cannot start (no WebGL in jsdom).
- Google Maps (mocked API): the traffic toggle replaces the 3D control and shows/hides the layer without reloading.
- No emergency banner; no time field and no client time sent; services panel; resolve with confirmation.
- Geolocation granted / denied / unsupported / outside the mapped area, manual override and a late position; speech
  input states (listening, transcript appended and editable, denied microphone, empty transcript, unsupported).
- Service colours and line patterns shared by routes and station markers; only ranked facility candidates mapped;
  a resolved incident leaves cached queues immediately; no internal state-version labels.

Tests never read the developer's map keys (`vite.config.ts` blanks them for Vitest). The real Google Maps provider,
`TrafficLayer` toggle, route visibility and resolved-marker removal were checked by hand in a browser with a configured
key; the real 3D renderer (MapLibre + OpenFreeMap) was checked by hand too. Neither is covered by automated tests.

Migration drift check, from `backend/`: `.venv/bin/python -m alembic check` (against a database at the head revision).

## Database migrations

| Revision | What it does | Effect on existing data |
|---|---|---|
| 0001 | Initial schema | n/a |
| 0002 | Adds cities, road-graph versions, road closures, operators, sessions, rate-limit events; adds citizen-report columns to incidents and operator ids to approvals and executions; repoints `simulation_routes` from fictional nodes to graph node ids; adds the append-only trigger on `audit_events` | Additive. Existing incidents are labelled `legacy` (read-only); fictional-city nodes are kept under dataset `legacy_meridian_bay`. Downgrading below 0002 is refused once routes on the real graph exist (the old schema cannot hold them); restore a backup instead |
| 0003 | Check constraints: `DISMISSED` status, incident source, coordinate ranges | Constraint-only; existing rows satisfy it |
| 0004 | Indexes citizen reports by source, city and creation time for the five-report public feed | Additive, no data transformation |
| 0005 | `RESOLVED` incident status; `resolved_at`, `resolved_by_id`, `resolution_note` | Additive (nullable columns, wider CHECK). Downgrade refuses while any incident is `RESOLVED` |

On first start after 0002, the backend registers Mangaluru and bumps the city state version, so legacy plans become
stale. Back up before migrating a database you care about:
`pg_dump -Fc -d gridpulse -f backups/gridpulse.dump` (the `backups/` folder is git-ignored).

## Known limitations

- Hackathon prototype, not production. There is no TLS termination, no account lockout notification, and no multi-factor authentication.
- Road data is a snapshot (`osm_base_timestamp` in `geodata info`). Travel times are free-flow estimates, not live traffic.
- AnalyseTraffic has no live traffic provider in this build; its findings are explicitly estimated/unavailable and never authorize real traffic control.
- Google Maps is optional browser presentation. A restricted browser key, billing/API authorization, quotas and provider terms require manual verification; Leaflet/OSM remains the local fallback.
- Only one fire station, 3 police stations and 5 traffic signals are mapped in OSM inside the coverage box. Fire & rescue routes start only at the fire station; police routes only at police stations. Station staffing and unit availability are unknown.
- Ambulance bases are not mapped; the medical route is a transfer from the incident to a reachable hospital.
- The 3D view (MapLibre GL + OpenFreeMap) extrudes OSM buildings; where OSM has no height or levels tag the tile schema uses a default, so heights are approximate. There is no terrain. It needs WebGL and internet access; it loads only when selected. OpenFreeMap's "dark" style is recoloured at load (and a light added) because unmodified it is near-black.
- With Google Maps, Google's own points of interest and transit icons are hidden so only GridPulse's ranked facilities show. The visual `TrafficLayer` depends on Google's coverage; it is never used as data.
- Speech recognition availability and privacy depend on the browser; GridPulse itself never receives audio.
- The sensor panel lists every synthetic observation near the pin while a demo scenario is loaded, even for unrelated report types; only hazard-relevant readings feed the evidence.
- The coverage box crops the city. Some edge areas look like dead ends that are not (see Scenario C).
- Rate limits key on the direct client address. Behind a reverse proxy, every client would share one key; `X-Forwarded-For` is deliberately not trusted.
- Pre-execution validation reuses the evidence captured during the workflow, and any change to city state makes outstanding approvals stale (deliberately conservative).
- Windows scripts are written and their dependencies were resolved for Windows, but they have not been run on Windows. Docker Compose has not been run.
