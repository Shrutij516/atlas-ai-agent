# Atlas — Agentic Travel & Lifestyle Assistant

Atlas is a full-stack AI chat application that helps users plan trips, check live weather, and stay on top of the news. Instead of guessing, Atlas **always fetches real data** before answering — then turns it into practical recommendations like what to pack, when to go out, or what's happening in a city.

This repo contains three services that work together: a data API (`mcp-server`), a LangChain agent backend powered by Groq (`backend`), and a browser chat UI (`frontend`).

---

## Project Overview

**Atlas** is a travel and lifestyle assistant with a friendly personality. Ask it things like:

- *"What's the weather in Tokyo right now?"*
- *"I'm visiting London for 4 days — what should I expect?"*
- *"What's the latest tech news?"*
- *"What's happening in Berlin this week?"*

Behind the scenes, Atlas uses **tool calling**: the LLM decides which data source to query, calls the appropriate tool, reads the live result, and writes a human-friendly answer. The chat UI shows live status updates while Atlas works (e.g. *"Atlas is checking the weather..."*).

### What each layer does

| Layer | Role |
|-------|------|
| **Frontend** | Chat UI — sends messages, streams status updates, displays Atlas replies |
| **Backend** | Groq-powered LangChain agent with 4 tools that call the MCP server |
| **MCP Server** | REST API wrapping OpenWeatherMap and NewsAPI |

### Agent tools

| Tool | Data source | Example use |
|------|-------------|-------------|
| `current_weather` | OpenWeatherMap | Current conditions for a city |
| `weather_forecast` | OpenWeatherMap | 1–5 day forecast |
| `news_headlines` | NewsAPI | Headlines by topic |
| `local_news` | NewsAPI | Local news for a city |

---

## Architecture

```
┌──────────────────┐   POST /chat/stream (SSE)   ┌──────────────────┐   HTTP GET    ┌──────────────────┐
│                  │ ───────────────────────────► │                  │ ────────────► │                  │
│     frontend     │                              │     backend      │               │    mcp-server    │
│   (static HTML)  │ ◄─────────────────────────── │  (FastAPI +      │ ◄──────────── │    (FastAPI)     │
│   port 3000      │   status events + response   │   LangChain +    │   JSON data   │    port 8001     │
│                  │                              │   Groq agent)    │               │                  │
└──────────────────┘                              │   port 8000      │               └────────┬─────────┘
                                                  └──────────────────┘                        │
                                                                                              │
                                                                    ┌─────────────────────────┴─────────────────────────┐
                                                                    │                                                     │
                                                                    ▼                                                     ▼
                                                          OpenWeatherMap API                                         NewsAPI
                                                          (weather + geocoding)                                      (headlines + search)
```

### Request flow

1. **User** sends a message in the browser (`frontend/index.html`).
2. **Frontend** POSTs to `http://localhost:8000/chat/stream` with the message and conversation history.
3. **Backend** runs the LangChain ReAct agent (Groq `llama-3.3-70b-versatile`). When the agent picks a tool, the backend streams a status event to the frontend.
4. **Backend tools** call the MCP server over HTTP (`GET /weather/current`, `/weather/forecast`, `/news/headlines`, `/news/local`).
5. **MCP server** geocodes cities, calls OpenWeatherMap and NewsAPI, and returns clean JSON.
6. **Backend** feeds tool results back to Groq, gets the final reply, and streams it to the frontend.
7. **Frontend** removes the status bubble and displays Atlas's response with a typewriter effect.

### Service summary

| Service | Folder | Stack | Default port |
|---------|--------|-------|--------------|
| MCP Server | `mcp-server/` | Python, FastAPI, httpx | `8001` |
| Backend | `backend/` | Python, FastAPI, LangChain, LangGraph, Groq | `8000` |
| Frontend | `frontend/` | Static HTML/CSS/JS | `3000` |

---

## Prerequisites

- **Python 3.10+** (3.9 may work but 3.10+ is recommended for LangChain)
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

# 4. Start backend (terminal 2)
cd backend
python -m venv .venv && source .venv/bin/activate
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

**Free tier:** generous rate limits on models like `llama-3.3-70b-versatile` (the project default). This model supports tool calling, which Atlas requires.

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
| `GROQ_MODEL` | No | Groq model ID (default: `llama-3.3-70b-versatile`) |
| `MCP_SERVER_URL` | No | MCP server base URL (default: `http://localhost:8001`) |
| `HOST` | No | Bind address (default: `0.0.0.0`) |
| `PORT` | No | Server port (default: `8000`) |
| `REQUEST_TIMEOUT_SECONDS` | No | MCP tool call timeout (default: `15`) |

Example:

```env
GROQ_API_KEY=your_groq_key_here
GROQ_MODEL=llama-3.3-70b-versatile
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
python -m venv .venv
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

### Terminal 3 — Frontend (start last)

```bash
cd frontend
python -m http.server 3000
```

Open **[http://localhost:3000](http://localhost:3000)** in your browser.

Try: *"What's the weather in San Francisco this week?"* — you should see a status message like *"Atlas is checking the forecast..."* followed by a recommendation.

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
│   ├── main.py                 # FastAPI app + /chat, /chat/stream
│   ├── agent.py                # LangChain + Groq agent setup
│   ├── tools.py                # 4 tools that call MCP server
│   ├── prompts.py              # Atlas system prompt
│   ├── status_messages.py      # Tool → status text mapping
│   ├── config.py
│   ├── schemas.py
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    └── index.html              # Chat UI
```

---

## Deployment with Docker Compose

For production, containerize each service and orchestrate them with Docker Compose. The frontend is static and can be served by nginx; the two Python services run as separate containers on an internal network.

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

### Example `docker-compose.yml`

This file is **not included in the repo** — it illustrates a production setup you can add:

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
# Add a custom nginx.conf to proxy /chat/stream → backend:8000
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

### Production checklist

| Concern | Recommendation |
|---------|----------------|
| **Secrets** | Inject via Docker secrets, a vault, or CI/CD — never bake keys into images |
| **CORS** | Restrict `allow_origins` in `backend/main.py` to your frontend domain |
| **Frontend API URL** | Replace hardcoded `http://localhost:8000` in `index.html` with your production backend URL, or proxy API calls through nginx |
| **HTTPS** | Terminate TLS at nginx or a load balancer (e.g. Caddy, Traefik, AWS ALB) |
| **Scaling** | Scale `backend` horizontally; keep `mcp-server` stateless and scale independently |
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
| Groq `tool_use_failed` | Model tool-call format issue | Ensure `GROQ_MODEL=llama-3.3-70b-versatile` |
| CORS error in browser | Frontend origin blocked | Backend allows `*` in dev; configure CORS for production |
| Frontend can't reach backend | Wrong API URL | Confirm `index.html` points to the running backend URL |

---

## License

MIT (or your chosen license — update as needed).
