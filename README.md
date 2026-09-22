# Atlas — Agentic Travel & Lifestyle Assistant

Atlas is a full-stack AI chat application that helps users plan trips, check live weather, stay on top of the news, and build a personal itinerary. Instead of guessing, Atlas **always fetches real data** before answering — then turns it into practical recommendations like what to pack, when to go out, or what's happening in a city. Any change to the user's saved itinerary pauses for human approval before it's written anywhere.

This repo contains three services that work together: a data API (`mcp-server`), a LangGraph multi-agent backend powered by Groq (`backend`), and a browser chat UI (`frontend`).

---

## Project Overview

**Atlas** is a travel and lifestyle assistant with a friendly personality. Ask it things like:

- *"What's the weather in Tokyo right now?"*
- *"I'm visiting London for 4 days — what should I expect?"*
- *"What's the latest tech news?"*
- *"What's happening in Berlin this week?"*
- *"Add a 3-night trip to Lisbon starting April 10"* — proposes an itinerary item, but won't save it until you approve it

Behind the scenes, a supervisor routes each message to one or more specialist sub-agents (weather, news, itinerary) that call live tools; an aggregator then combines whatever ran into a single human-friendly reply. The chat UI shows live status updates while Atlas works (e.g. *"Atlas is checking the weather..."*), and itinerary changes show up as an approval card instead of being saved silently.

### What each layer does

| Layer | Role |
|-------|------|
| **Frontend** | Chat UI — sends messages, streams status updates, displays replies, and renders itinerary approval cards plus an audit log viewer |
| **Backend** | Groq-powered LangGraph multi-agent graph (supervisor, specialist sub-agents, aggregator) with 5 tools; itinerary writes pause for human approval and are recorded in a SQLite audit trail |
| **MCP Server** | REST API wrapping OpenWeatherMap and NewsAPI |

### Agent tools

| Tool | Data source | Example use |
|------|-------------|-------------|
| `current_weather` | OpenWeatherMap | Current conditions for a city |
| `weather_forecast` | OpenWeatherMap | 1–5 day forecast |
| `news_headlines` | NewsAPI | Headlines by topic |
| `local_news` | NewsAPI | Local news for a city |
| `propose_itinerary_item` | SQLite (`atlas.db`) | Propose saving a trip (city + dates + notes) — pauses for human approval before anything is written |

---

## Architecture

```
┌──────────────────┐   POST /chat/stream (SSE)   ┌──────────────────┐   HTTP GET    ┌──────────────────┐
│                  │ ───────────────────────────► │                  │ ────────────► │                  │
│     frontend     │                              │     backend      │               │    mcp-server    │
│   (static HTML)  │ ◄─────────────────────────── │  (FastAPI +      │ ◄──────────── │    (FastAPI)     │
│   port 3000      │   status/pending_approval/   │   LangGraph      │   JSON data   │    port 8001     │
│                  │   done SSE events            │   multi-agent    │               │                  │
└──────────────────┘                              │   graph + Groq)  │               └────────┬─────────┘
                                                  │   port 8000      │                        │
                                                  │        │         │                        │
                                                  │        ▼         │       ┌────────────────┴────────────────┐
                                                  │  atlas.db        │       ▼                                 ▼
                                                  │  (SQLite:        │  OpenWeatherMap API                  NewsAPI
                                                  │  itinerary,      │  (weather + geocoding)          (headlines + search)
                                                  │  audit log,      │
                                                  │  checkpoints)    │
                                                  └──────────────────┘
```

### The backend graph (`backend/graph.py`)

The backend is no longer a single ReAct agent — it's a LangGraph `StateGraph` with a supervisor, three specialist sub-agents that can run in parallel, and an aggregator that composes the final reply:

```
        conversation history + new message
                    │
                    ▼
            ┌───────────────┐
            │   supervisor   │   structured-output routing decision:
            │                │   which of weather / news / itinerary
            └───────┬────────┘   does this message need? (can be several,
                    │            or none for plain conversation)
                    │
     fan out — every matching sub-agent runs in parallel
                    │
      ┌─────────────┼──────────────────┐
      ▼             ▼                  ▼
┌───────────┐ ┌───────────┐   ┌─────────────────────┐
│ weather_  │ │  news_    │   │   itinerary_agent    │
│ agent     │ │  agent    │   │                      │
│           │ │           │   │ propose_itinerary_   │
│ current_  │ │ news_     │   │   item(city, dates,  │
│ weather,  │ │ headlines,│   │   notes)             │
│ weather_  │ │ local_news│   │        │             │
│ forecast  │ │           │   │        ▼             │
└─────┬─────┘ └─────┬─────┘   │  interrupt() — see   │
      │             │         │  "Human-in-the-Loop" │
      │             │         │  below               │
      │             │         └──────────┬───────────┘
      └──────┬──────┴────────────────────┘
             ▼
      ┌──────────────┐
      │  aggregator   │   combines whichever sub-agent
      │               │   result(s) actually ran into
      └──────┬────────┘   ONE final reply
             ▼
      final AIMessage → streamed to the frontend as SSE
```

### Request flow

1. **User** sends a message in the browser (`frontend/index.html`), tagged with a `conversation_id` that stays stable for the whole chat session.
2. **Frontend** POSTs to `http://localhost:8000/chat/stream` with the message, conversation history, and `conversation_id`.
3. **Backend**'s `supervisor` node (a structured-output Groq call) decides which specialist sub-agents the message needs — `weather_agent`, `news_agent`, `itinerary_agent`, any combination, or none.
4. Every selected sub-agent runs **in parallel** in the same step. `weather_agent`/`news_agent` call the MCP server over HTTP and return factual findings; `itinerary_agent` calls `propose_itinerary_item`, which pauses the whole graph for human approval (see below) if a save is proposed.
5. **MCP server** geocodes cities, calls OpenWeatherMap and NewsAPI, and returns clean JSON.
6. Once every selected branch has finished (or the itinerary branch is paused awaiting approval), the **aggregator** node composes one final, persona-consistent reply from whatever findings are available.
7. **Frontend** removes the status bubble and displays the reply — or, if the graph paused, renders an approval card instead.

### Service summary

| Service | Folder | Stack | Default port |
|---------|--------|-------|--------------|
| MCP Server | `mcp-server/` | Python, FastAPI, httpx | `8001` |
| Backend | `backend/` | Python, FastAPI, LangChain, LangGraph, Groq, SQLite | `8000` |
| Frontend | `frontend/` | Static HTML/CSS/JS | `3000` |

---

## Human-in-the-Loop Itinerary Approval

Nothing is ever written to the itinerary without an explicit human decision. When `itinerary_agent` calls `propose_itinerary_item`:

```
POST /chat/stream                                    POST /approve
        │                                                   ▲
        ▼                                                   │
 supervisor → itinerary_agent → propose_itinerary_item(...)  │
                                        │                    │
                          assess_itinerary_risk()            │
                          deterministic, no LLM call:         │
                          • start date in the past  → high    │
                          • end date before start   → high    │
                          • overlaps an existing trip → medium │
                          • longer than 21 nights     → medium │
                          • otherwise                 → low    │
                                        │                    │
                          one cheap extra LLM call →           │
                          short human-readable context note    │
                                        │                    │
                                        ▼                    │
                                  interrupt(payload) ─────────┘
                                        │      graph execution pauses here;
                                        │      state is checkpointed to
                                        │      atlas.db (LangGraph's
                                        │      AsyncSqliteSaver) so the pause
                                        │      survives the HTTP round trip
                                        ▼
                        SSE event: pending_approval
                        { thread_id, proposed_item, risk, context_note }
```

1. **Risk assessment is deterministic**, not LLM-judged (`backend/risk.py`) — the same inputs always produce the same risk level and reasons, so it can't be talked out of flagging a real problem.
2. **A separate, cheap LLM call** adds a one-line human-readable context note (e.g. *"This is a 30-night stay, unusually long"*) — informational only, not part of the risk decision.
3. The graph then calls LangGraph's `interrupt()`, which pauses execution and returns a `pending_approval` SSE event instead of a final reply. The paused state is checkpointed to `atlas.db`, so it survives across the HTTP request boundary until someone resolves it.
4. A human resolves it — via the approval card in the UI, or directly with `POST /approve` — with one of three decisions:
   - **`approve`** — saves exactly what was proposed.
   - **`edit`** — saves human-corrected city/dates/notes instead of the original proposal.
   - **`reject`** — nothing is saved.
5. Every proposal and its eventual decision is recorded in an **audit trail** (the `audit_log` table in `atlas.db`): proposed values, risk level and reasons, the context note, the human decision, the final (possibly edited) values, and both a `proposed_at` and `decided_at` timestamp. View it via `GET /audit-log` or the "View audit log" panel in the UI.

**Note:** while a proposal is pending on a given `conversation_id`, sending another chat message on that same conversation is blocked (the backend re-surfaces the same pending card) rather than silently abandoning the paused proposal — resolve it first via approve/edit/reject.

---

## Prerequisites

- **Python 3.11 specifically** for the backend (not 3.10 or earlier) — LangGraph's `interrupt()` relies on `contextvars` propagation through async execution that only works reliably on Python ≥ 3.11. `mcp-server` and the static frontend have no such constraint.
- **pip** and **venv**
- A modern web browser
- Free API keys from OpenWeatherMap, NewsAPI, and Groq (see below)

---

## Quick Start (~10 minutes)

```bash
# 1. Clone the repo
git clone <your-repo-url>
cd atlas-ai-agent

# 2. Set up API keys (see sections below), then configure .env files
cp mcp-server/.env.example mcp-server/.env
cp backend/.env.example backend/.env
# Edit both .env files and paste your keys

# 3. Start MCP server (terminal 1)
cd mcp-server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8001

# 4. Start backend (terminal 2) — must be Python 3.11
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# 5. Start frontend (terminal 3)
cd frontend
python -m http.server 3000

# 6. Open the app
# → http://localhost:3000
```

**Windows:** replace `source .venv/bin/activate` with `.venv\Scripts\activate`.

Verify the stack is healthy:

- MCP server: [http://localhost:8001/health](http://localhost:8001/health)
- Backend: [http://localhost:8000/health](http://localhost:8000/health)
- API docs: [http://localhost:8001/docs](http://localhost:8001/docs) and [http://localhost:8000/docs](http://localhost:8000/docs)

---

## Getting API Keys (Free Tiers)

All three providers offer free tiers sufficient for local development.

### 1. OpenWeatherMap

Used by the MCP server for current weather, forecasts, and city geocoding.

1. Go to [https://openweathermap.org/api](https://openweathermap.org/api) and click **Sign Up** (top right), or register directly at [https://home.openweathermap.org/users/sign_up](https://home.openweathermap.org/users/sign_up).
2. Confirm your email address.
3. Open [https://home.openweathermap.org/api_keys](https://home.openweathermap.org/api_keys).
4. Copy the default API key (or create a new one).
5. **Note:** new keys can take up to 2 hours to activate. If you get auth errors immediately after signup, wait and retry.

**Free tier:** 1,000 calls/day for Current Weather and 5-day Forecast.

### 2. NewsAPI

Used by the MCP server for topic headlines and local news search.

1. Go to [https://newsapi.org/register](https://newsapi.org/register).
2. Fill in your name, email, and intended use (e.g. *"Personal development project"*).
3. After registration, your API key is shown on the dashboard at [https://newsapi.org/account](https://newsapi.org/account).

**Free tier (Developer plan):** 100 requests/day. Local news uses the `/everything` endpoint with city-based search.

### 3. Groq

Used by the backend as the LLM for Atlas's reasoning and tool calling.

1. Go to [https://console.groq.com](https://console.groq.com) and sign up.
2. Open [https://console.groq.com/keys](https://console.groq.com/keys).
3. Click **Create API Key**, give it a name, and copy the key.

**Free tier:** generous rate limits on models that support tool calling, which Atlas requires (the project default is `openai/gpt-oss-120b`). See the note under [Environment Variables](#environment-variables) below — Groq's available model list changes, so this default can drift.

---

## Environment Variables

### `mcp-server/.env`

Copy the example file and fill in your keys:

```bash
cp mcp-server/.env.example mcp-server/.env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENWEATHERMAP_API_KEY` | Yes | Your OpenWeatherMap API key |
| `NEWSAPI_KEY` | Yes | Your NewsAPI key |
| `HOST` | No | Bind address (default: `0.0.0.0`) |
| `PORT` | No | Server port (default: `8001`) |
| `REQUEST_TIMEOUT_SECONDS` | No | Upstream API timeout (default: `10`) |

Example:

```env
OPENWEATHERMAP_API_KEY=your_openweathermap_key_here
NEWSAPI_KEY=your_newsapi_key_here
HOST=0.0.0.0
PORT=8001
REQUEST_TIMEOUT_SECONDS=10
```

### `backend/.env`

```bash
cp backend/.env.example backend/.env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | Yes | Your Groq API key |
| `GROQ_MODEL` | No | Groq model ID (default: `openai/gpt-oss-120b`) — must support tool calling |
| `MCP_SERVER_URL` | No | MCP server base URL (default: `http://localhost:8001`) |
| `HOST` | No | Bind address (default: `0.0.0.0`) |
| `PORT` | No | Server port (default: `8000`) |
| `REQUEST_TIMEOUT_SECONDS` | No | MCP tool call timeout (default: `15`) |

> **Note:** Groq periodically retires models — the default above has already changed once this way (the original default, `llama-3.3-70b-versatile`, was decommissioned). If `GROQ_MODEL` starts failing with a 404 / `model_not_found`, check `GET https://api.groq.com/openai/v1/models` (with your `GROQ_API_KEY` as a bearer token) for what's currently available on your account, and set `GROQ_MODEL` to one that supports tool calling.

Example:

```env
GROQ_API_KEY=your_groq_key_here
GROQ_MODEL=openai/gpt-oss-120b
MCP_SERVER_URL=http://localhost:8001
HOST=0.0.0.0
PORT=8000
REQUEST_TIMEOUT_SECONDS=15
```

> **Never commit `.env` files.** They are listed in `.gitignore`. Only commit `.env.example`.

---

## Running the Services

Start services **in this order** — the backend depends on the MCP server, and the frontend depends on the backend.

### Terminal 1 — MCP Server (start first)

```bash
cd mcp-server
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # skip if already configured
uvicorn main:app --reload --port 8001
```

MCP endpoints:

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /weather/current?city=London` | Current weather |
| `GET /weather/forecast?city=London&days=3` | Multi-day forecast (1–5 days) |
| `GET /news/headlines?topic=technology` | Headlines by topic |
| `GET /news/local?city=Berlin` | Local news by city |

### Terminal 2 — Backend (start second)

```bash
cd backend
python3.11 -m venv .venv           # must be 3.11 — see Prerequisites
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # skip if already configured
uvicorn main:app --reload --port 8000
```

Backend endpoints:

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `POST /chat` | Synchronous chat (JSON response) |
| `POST /chat/stream` | Streaming chat with tool status events (used by frontend) |
| `POST /approve` | Resolve a pending itinerary proposal — `{thread_id, decision: "approve"\|"edit"\|"reject", edited_item?}`, streamed like `/chat/stream` |
| `GET /audit-log` | Most recent itinerary proposals/decisions (capped at 50), most recent first |

### Terminal 3 — Frontend (start last)

```bash
cd frontend
python -m http.server 3000
```

Open **[http://localhost:3000](http://localhost:3000)** in your browser.

Try: *"What's the weather in San Francisco this week?"* — you should see a status message like *"Atlas is checking the forecast..."* followed by a recommendation. Try *"Add a 3-night trip to Lisbon starting April 10"* to see the approval card.

---

## Project Structure

```
atlas-ai-agent/
├── README.md
├── .gitignore
├── mcp-server/
│   ├── main.py                 # FastAPI app entry point
│   ├── config.py               # Environment config
│   ├── requirements.txt
│   ├── .env.example
│   ├── routers/
│   │   ├── weather.py          # /weather/current, /weather/forecast
│   │   └── news.py             # /news/headlines, /news/local
│   └── services/
│       ├── openweather.py      # OpenWeatherMap client + geocoding
│       └── newsapi.py          # NewsAPI client
├── backend/
│   ├── main.py                 # FastAPI app: /chat, /chat/stream, /approve, /audit-log
│   ├── agent.py                # Re-exports graph.py's compiled graph as get_agent()
│   ├── graph.py                # LangGraph StateGraph: supervisor, sub-agents, aggregator
│   ├── tools.py                # 5 tools; propose_itinerary_item calls interrupt()
│   ├── db.py                   # SQLite helpers: itinerary_items, audit_log tables
│   ├── risk.py                 # Deterministic (no LLM) itinerary risk assessment
│   ├── prompts.py              # Atlas system prompt (used by the aggregator)
│   ├── status_messages.py      # Tool/node → status text mapping
│   ├── config.py
│   ├── schemas.py               # Adds ApproveRequest, PendingApproval, AuditLogEntry, etc.
│   ├── requirements.txt
│   ├── .env.example
│   ├── atlas.db                 # SQLite DB — created at runtime, gitignored
│   └── .venv/                   # Python 3.11 virtualenv — gitignored
└── frontend/
    └── index.html              # Chat UI + approval cards + audit log viewer
```

---

## Deployment with Docker Compose

For production, containerize each service and orchestrate them with Docker Compose. The frontend is static and served by nginx, which also reverse-proxies API calls to the backend; the two Python services run as separate containers on an internal network.

`docker-compose.yml` and each service's `Dockerfile`/`nginx.conf` are included in this repo — the excerpts below are for reference, not hypothetical examples.

### Recommended production layout

```
┌─────────────────────────────────────────────────────────────┐
│                     docker-compose network                   │
│                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐      │
│  │   nginx     │    │   backend   │    │ mcp-server  │      │
│  │  (frontend) │───►│  :8000      │───►│  :8001      │      │
│  │  :80/443    │    │             │    │             │      │
│  └─────────────┘    └─────────────┘    └─────────────┘      │
│        ▲                                                     │
└────────┼─────────────────────────────────────────────────────┘
         │
    Internet / users
```

nginx (`frontend/nginx.conf`) proxies every backend route the frontend calls, not just chat: `/chat/stream` and `/approve` are both SSE (buffering/caching off, `Connection ""`, a long `proxy_read_timeout`, since both stream status events before a final result), while `/chat`, `/audit-log`, and `/health` are plain request/response proxies.

### Example `docker-compose.yml`

```yaml
services:
  mcp-server:
    build: ./mcp-server
    env_file: ./mcp-server/.env
    expose:
      - "8001"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
      interval: 30s
      timeout: 5s
      retries: 3

  backend:
    build: ./backend
    env_file: ./backend/.env
    environment:
      MCP_SERVER_URL: http://mcp-server:8001
    ports:
      - "8000:8000"
    depends_on:
      mcp-server:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3

  frontend:
    build: ./frontend
    ports:
      - "80:80"
    depends_on:
      - backend
    restart: unless-stopped
```

### Example Dockerfiles

**`mcp-server/Dockerfile`**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
```

**`backend/Dockerfile`**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**`frontend/Dockerfile`**

```dockerfile
FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
# nginx.conf proxies /chat/stream, /chat, /approve, /audit-log, and /health to backend:8000
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

### ⚠️ Known limitation: `atlas.db` has no persistent volume

`atlas.db` holds `itinerary_items`, the `audit_log` audit trail, **and** LangGraph's own checkpoint tables for any itinerary proposal currently paused awaiting approval. Right now it lives only inside the backend container's writable layer — no volume is mounted for it, either in the Dockerfiles above or in `docker-compose.yml`.

That means:

- It survives a plain container restart (`restart: unless-stopped` keeps the same container), but is **permanently lost** on any container replacement — a rebuild, a redeploy, `docker compose up --build`, or `docker rm`.
- Scaling `backend` to more than one replica gives each replica its own independent file, so itinerary data and audit history silently diverge across them.
- An itinerary proposal a human hasn't approved or rejected yet is discarded with no warning if the container is torn down first — the checkpoint that would let `/approve` resume it is gone.

**Before deploying this anywhere that redeploys**, mount a named volume for `atlas.db` and make `backend/db.py`'s `DB_PATH` configurable via an environment variable so it can point at the mounted path. This is a real gap, not a hardening nice-to-have, for a feature whose entire point is a persistent, reviewable audit trail.

### Production checklist

| Concern | Recommendation |
|---------|----------------|
| **Secrets** | Inject via Docker secrets, a vault, or CI/CD — never bake keys into images |
| **Persistent data** | Mount a volume for `atlas.db` and make `DB_PATH` configurable — see "Known limitation" above |
| **CORS** | Restrict `allow_origins` in `backend/main.py` to your frontend domain |
| **Frontend API URL** | Replace hardcoded `http://localhost:8000` in `index.html` with your production backend URL, or proxy API calls through nginx |
| **HTTPS** | Terminate TLS at nginx or a load balancer (e.g. Caddy, Traefik, AWS ALB) |
| **Scaling** | Scale `backend` horizontally only after fixing the `atlas.db` persistence gap above; keep `mcp-server` stateless and scale independently |
| **Rate limits** | OpenWeatherMap (1k/day) and NewsAPI (100/day) free tiers are dev-only — upgrade for production traffic |
| **Logging** | Add structured logging and health-check monitoring for all three services |
| **MCP server exposure** | Do **not** expose `mcp-server` publicly — keep it on the internal Docker network; only `backend` should reach it |

### Deploy commands

```bash
# Build and start all services
docker compose up -d --build

# View logs
docker compose logs -f backend

# Tear down
docker compose down
```

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---------|--------------|-----|
| `GROQ_API_KEY is not set` | Missing backend `.env` | Copy `backend/.env.example` → `.env` and add your key |
| `Could not reach the MCP server` | MCP server not running | Start `mcp-server` on port 8001 before the backend |
| OpenWeatherMap 401 | Key not activated yet | Wait up to 2 hours after signup |
| NewsAPI rate limit | Free tier exceeded (100/day) | Wait until reset or upgrade plan |
| Groq `tool_use_failed` | Model tool-call format issue | Ensure `GROQ_MODEL` is set to a model that supports tool calling (default: `openai/gpt-oss-120b`) |
| Groq `model_not_found` (404) | Configured model was decommissioned | Check `GET https://api.groq.com/openai/v1/models` for what's currently available and update `GROQ_MODEL` |
| CORS error in browser | Frontend origin blocked | Backend allows `*` in dev; configure CORS for production |
| Frontend can't reach backend | Wrong API URL | Confirm `index.html` points to the running backend URL |

---

## License

MIT (or your chosen license — update as needed).
