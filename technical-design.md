# Technical Design: Deploy `qwen3-embedding:0.6b` Embedding API on Mac Mini M4

## Goal

Run the `qwen3-embedding:0.6b` embedding model locally on a Mac Mini M4 and expose it as a public HTTP API with API key authentication. Clients (typically RAG pipelines) submit text and get back dense vectors.

## Architecture

```
┌───────────────────────────── Mac Mini M4 (host) ─────────────────────────────┐
│                                                                              │
│   Ollama (native, Metal GPU)                                                 │
│   listens on 127.0.0.1:11434                                                 │
│         ▲                                                                    │
│         │ host.docker.internal:11434                                         │
│         │                                                                    │
│   ┌─────┴──────────────── Docker (compose) ────────────────┐                 │
│   │                                                        │                 │
│   │   FastAPI (uvicorn) ──── Cloudflare Tunnel ──► public  │                 │
│   │   :8080  (API key auth)                                │                 │
│   │                                                        │                 │
│   └────────────────────────────────────────────────────────┘                 │
└──────────────────────────────────────────────────────────────────────────────┘

Internet → Cloudflare Tunnel → FastAPI container (API key auth) → Ollama on host
```

Ollama runs **natively on the Mac Mini M4 host** so it can use the Metal GPU — Linux Docker containers on macOS cannot access Metal. Everything else (FastAPI, Cloudflare Tunnel) runs inside containers managed by `docker-compose`. The FastAPI container reaches Ollama via `host.docker.internal`, which Docker Desktop maps to the host's loopback interface.

### Components

| Component             | Where it runs           | Role                                                                      |
| --------------------- | ----------------------- | ------------------------------------------------------------------------- |
| **Ollama**            | Host (native, Metal)    | Loads `qwen3-embedding:0.6b` and serves embeddings on `127.0.0.1:11434`.  |
| **FastAPI**           | Docker container        | Validates API key, forwards requests to Ollama, returns embedding JSON.   |
| **Cloudflare Tunnel** | Docker container        | Exposes the FastAPI container on a stable public URL — no port forwarding.|

### Why these choices

- **Ollama on host (not in a container)**: Metal GPU passthrough doesn't work for Linux containers on macOS. Running Ollama natively gives Apple Silicon acceleration; running it in a Linux container forces CPU-only inference.
- **FastAPI over nginx**: We need typed request/response schemas, more flexible auth (per-key rate limits later, key rotation, audit logs), and the embedding endpoint already returns small JSON (no SSE streaming required), so nginx's main advantage — efficient proxying of long-lived streams — doesn't apply.
- **Cloudflare Tunnel over ngrok / port forwarding**: Free, stable URL, no router config, built-in DDoS protection.

## Embedding Model Options on Ollama

Ollama's library carries the major open embedding models. All of the ones below run comfortably on a Mac Mini M4 (24 GB unified memory, Metal); the practical question is quality vs. throughput vs. memory headroom, since Ollama keeps the model resident between requests.

> **Note on Gemini:** Google's Gemini embedding models (`text-embedding-004`, `gemini-embedding-001`) are **not** available on Ollama — they're proprietary, hosted only on Google's API. The closest open option from Google is `embeddinggemma`, listed below.

| Ollama tag                        | Family / vendor      | Params  | Dim   | Context | Multilingual | Throughput on M4 24 GB † | Notes                                                                |
| --------------------------------- | -------------------- | ------- | ----- | ------- | ------------ | ------------------------ | -------------------------------------------------------------------- |
| `qwen3-embedding:0.6b` ★          | Qwen 3 (Alibaba)     | 0.6 B   | 1024  | 32 k    | yes (100+)   | ~4k–8k tok/s             | **Current choice.** Long context, strong multilingual, modest size.  |
| `qwen3-embedding:4b`              | Qwen 3 (Alibaba)     | 4 B     | 2560  | 32 k    | yes          | ~400–800 tok/s           | Higher quality, ~2.5 GB resident, much slower per call.              |
| `qwen3-embedding:8b`              | Qwen 3 (Alibaba)     | 8 B     | 4096  | 32 k    | yes          | ~150–300 tok/s           | Top of the Qwen line; ~5 GB resident, fits cleanly on 24 GB.         |
| `embeddinggemma`                  | Gemma 3 (Google)     | 308 M   | 768   | 2 k     | yes (100+)   | ~10k–20k tok/s           | Released 2025, MRL truncation supported (768→512/256/128).           |
| `nomic-embed-text`                | Nomic AI             | 137 M   | 768   | 8 k     | English+     | ~20k–35k tok/s           | Very popular open default; fast, beats `text-embedding-ada-002`.     |
| `mxbai-embed-large`               | Mixedbread AI        | 335 M   | 1024  | 512     | English      | ~8k–15k tok/s            | Strong on MTEB English; short context limits long-doc use.           |
| `bge-m3`                          | BAAI                 | 567 M   | 1024  | 8 k     | yes (100+)   | ~4k–8k tok/s             | Dense + sparse + multi-vector retrieval; good multilingual baseline. |
| `bge-large` / `bge-large-en-v1.5` | BAAI                 | 335 M   | 1024  | 512     | English      | ~8k–15k tok/s            | Established English baseline.                                        |
| `snowflake-arctic-embed:33m`      | Snowflake            | 33 M    | 384   | 512     | English      | ~75k–125k tok/s          | Tiny, very fast, OK quality for high-volume RAG.                     |
| `snowflake-arctic-embed:335m`     | Snowflake            | 335 M   | 1024  | 512     | English      | ~8k–15k tok/s            | Best of v1 family.                                                   |
| `snowflake-arctic-embed2`         | Snowflake            | 568 M   | 1024  | 8 k     | yes          | ~4k–8k tok/s             | v2; multilingual, longer context.                                    |
| `granite-embedding:30m`           | IBM Granite          | 30 M    | 384   | 512     | English      | ~75k–125k tok/s          | Tiny English-only.                                                   |
| `granite-embedding:278m`          | IBM Granite          | 278 M   | 768   | 512     | yes (12)     | ~10k–20k tok/s           | Multilingual variant.                                                |
| `all-minilm`                      | sentence-transformers| 23 M    | 384   | 256     | English      | ~100k–150k tok/s         | Classic baseline; lowest memory, highest throughput.                 |
| `paraphrase-multilingual`         | sentence-transformers| 278 M   | 768   | 128     | yes (50+)    | ~10k–20k tok/s           | Old but solid for short multilingual snippets.                       |

★ = the model this service serves.

† **Rough order-of-magnitude estimate, in tokens/sec** — not measured. Assumes: M4 (10-core GPU) Mac Mini with 24 GB unified memory, Ollama running natively (Metal), warm model (already loaded), batched calls (one HTTP request with many items in the `input` array), English text. Tokens/sec stays roughly stable across input length once batched; single-item HTTP calls (one `input` per request) are dominated by request overhead and the effective per-token rate drops substantially. As a quick conversion: at ~4 chars/token for English, multiply by ~4 for chars/sec; divide by ~50 for embeddings/sec on typical short chunks. **Benchmark with your real workload before relying on these numbers** — the goal here is to compare models against each other, not to commit to absolute SLOs.

### Picking on a Mac Mini M4 (24 GB)

- **For most RAG (English or mixed):** `nomic-embed-text` or `qwen3-embedding:0.6b`. Both fit in <1 GB, embed hundreds of short chunks per second on Metal, and have ≥8k context so you don't have to chunk aggressively.
- **For multilingual / long context:** `qwen3-embedding:0.6b` or `bge-m3`. Both handle 8k+ tokens and 100+ languages.
- **For maximum throughput on huge corpora:** `all-minilm` or `snowflake-arctic-embed:33m` — 384-dim vectors are 2.6× cheaper to store and compare than 1024-dim, and these models embed in ~0.5–1 ms per short input on Metal.
- **For maximum quality, willing to pay latency:** `qwen3-embedding:4b` or `bge-m3`. The 4B Qwen is the strongest open embedding model on MTEB at the time of writing, but expect ~10× the per-call latency of the 0.6B.
- **24 GB headroom:** `qwen3-embedding:8b` (~5 GB resident) is feasible here, unlike on the 16 GB mini, but the throughput hit is severe (~150–300 tok/s, ~25–50× slower than the 0.6B). Only worth it if quality matters more than indexing speed and your corpus is small.

To switch models, pull the new tag with `ollama pull <tag>` and update the `MODEL` constant in [app/main.py](app/main.py) (and the `model` field clients send). Vector dimension changes with the model, so any existing index has to be re-embedded.

## Tech Stack

| Tool                       | Purpose                                                                    |
| -------------------------- | -------------------------------------------------------------------------- |
| **Ollama**                 | Local embedding runtime, optimized for Apple Silicon (Metal GPU).          |
| **`qwen3-embedding:0.6b`** | The model being served (1024-dim embeddings, ~640 MB on disk).             |
| **FastAPI**                | API server: API key auth, schema validation, proxies to Ollama.            |
| **Uvicorn**                | ASGI server that runs the FastAPI app inside the container.                |
| **httpx (async)**          | HTTP client used by FastAPI to call Ollama on the host.                    |
| **Pydantic**               | Request/response schema validation in FastAPI.                             |
| **Python 3.11+**           | Runtime for the FastAPI service.                                           |
| **Docker / Compose**       | Packages and orchestrates FastAPI + Cloudflare Tunnel containers.          |
| **Cloudflare Tunnel** (`cloudflared`) | Exposes the API publicly without port forwarding.               |

## Detailed Design

### 1. Ollama — Embedding Server (on host)

Install and run on the Mac Mini M4 directly (not in Docker):

```bash
brew install ollama
ollama serve                         # starts on 127.0.0.1:11434
ollama pull qwen3-embedding:0.6b     # downloads the embedding model
```

Ollama exposes:
- `POST /v1/embeddings` — OpenAI-compatible embeddings endpoint.
- `POST /api/embed` — native Ollama endpoint.
- `GET  /v1/models`  — list installed models.

We proxy only `/v1/*`.

To keep Ollama reachable from the FastAPI container, ensure it binds to all interfaces (or rely on Docker Desktop's `host.docker.internal` mapping to loopback — works by default on Docker Desktop for Mac):

```bash
# (optional) bind to all interfaces if host.docker.internal mapping isn't enough
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

### 2. FastAPI — API Key Gate + Proxy (in container)

Single-file service. Reads `QWEN_API_KEY` and `OLLAMA_BASE_URL` from env (the latter defaulting to `http://host.docker.internal:11434`). Verifies the `Authorization: Bearer <key>` header with `secrets.compare_digest`, then forwards the body to Ollama using `httpx.AsyncClient` and returns the JSON response unchanged.

```python
# app/main.py (sketch)
import os, secrets
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import httpx

API_KEY = os.environ["QWEN_API_KEY"]
OLLAMA = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
MODEL  = "qwen3-embedding:0.6b"

app = FastAPI()
client = httpx.AsyncClient(base_url=OLLAMA, timeout=30.0)

class EmbeddingsRequest(BaseModel):
    model: str
    input: str | list[str]

def require_key(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, {"error": "unauthorized"})
    if not secrets.compare_digest(authorization.removeprefix("Bearer "), API_KEY):
        raise HTTPException(401, {"error": "unauthorized"})

@app.post("/v1/embeddings")
async def embeddings(req: EmbeddingsRequest, authorization: str | None = Header(None)):
    require_key(authorization)
    r = await client.post("/v1/embeddings", json=req.model_dump())
    if r.status_code >= 500:
        raise HTTPException(502, {"error": "upstream unavailable"})
    return r.json()

@app.get("/v1/models")
async def models(authorization: str | None = Header(None)):
    require_key(authorization)
    r = await client.get("/v1/models")
    return r.json()

@app.get("/health")
async def health():
    try:
        r = await client.get("/v1/models", timeout=2.0)
        ok = r.status_code == 200
    except Exception:
        ok = False
    return ({"status": "ok", "ollama": "reachable"} if ok
            else ({"status": "degraded", "ollama": "down"}, 503))
```

Container image (Dockerfile sketch):

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### 3. Cloudflare Tunnel — Public Access (in container)

Runs as a sibling container in `docker-compose.yml`, pointing at the FastAPI service by Compose service name:

```yaml
cloudflared:
  image: cloudflare/cloudflared
  command: tunnel --url http://api:8080
  depends_on:
    api:
      condition: service_started
```

For a free `*.trycloudflare.com` URL (quick-start), no Cloudflare account needed. For a stable subdomain, run `cloudflared tunnel login && tunnel create qwen-embed && tunnel route dns ...` once on the host and mount the resulting credentials into the container.

### 4. Development Environment (devcontainer)

The repo ships a Claude Code devcontainer (`.devcontainer/`) so contributors can iterate on the FastAPI code without local Python setup. It inherits the same Metal-on-host constraint as production:

- **Host file access**: only `${localWorkspaceFolder}` is bind-mounted to `/workspace`. The container cannot read other paths on the Mac host (no `~`, no `/Users/...`). This is intentional — Claude Code is sandboxed to the project tree.
- **Ollama access from the devcontainer**: same pattern as the production FastAPI container — Ollama runs natively on the Mac host (Metal/MLX is not available to Linux containers on macOS), and the devcontainer reaches it at `http://host.docker.internal:11434`.
- **Firewall**: the devcontainer runs `init-firewall.sh` (granted via `--cap-add=NET_ADMIN`) which sets the default OUTPUT policy to `DROP` and allows only an explicit allowlist plus the host's `/24` network (derived from the default route gateway). On Docker Desktop for Mac that subnet usually already covers `host.docker.internal`, so calls to Ollama work out of the box — but verify after rebuilding the container with `curl http://host.docker.internal:11434/v1/models`. If it fails, add an explicit allowance for the host gateway IP on port `11434` to `init-firewall.sh`.

### 5. Compose wiring

```yaml
services:
  api:
    build: .
    environment:
      - QWEN_API_KEY=${QWEN_API_KEY}
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
    extra_hosts:
      - "host.docker.internal:host-gateway"  # required on Linux; harmless on Docker Desktop for Mac
    ports:
      - "127.0.0.1:8080:8080"  # local debug only — public access is via the tunnel
    restart: unless-stopped

  cloudflared:
    image: cloudflare/cloudflared
    command: tunnel --url http://api:8080
    depends_on: [api]
    restart: unless-stopped
```

## API

All endpoints are served by FastAPI on port `8080` inside the container, exposed publicly via Cloudflare Tunnel. Every `/v1/*` request must include `Authorization: Bearer <API_KEY>`; missing or wrong keys return `401`.

### `POST /v1/embeddings`

OpenAI-compatible embeddings. FastAPI validates the API key, then proxies the body to Ollama at `${OLLAMA_BASE_URL}/v1/embeddings`.

**Request headers**
- `Authorization: Bearer <API_KEY>` (required)
- `Content-Type: application/json`

**Request body**

| Field           | Type                  | Required | Notes                                                       |
| --------------- | --------------------- | -------- | ----------------------------------------------------------- |
| `model`         | string                | yes      | Must be `qwen3-embedding:0.6b`.                             |
| `input`         | string \| string[]    | yes      | Single text or batch. Each item embedded independently.     |
| `encoding_format` | `"float"` \| `"base64"` | no   | Default `"float"`. Forwarded to Ollama.                     |

**Response (`200`)**

```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "index": 0,
      "embedding": [0.0123, -0.0456, /* ... 1024 floats ... */]
    }
  ],
  "model": "qwen3-embedding:0.6b",
  "usage": {"prompt_tokens": 8, "total_tokens": 8}
}
```

For batch input (array of N strings), `data` contains N entries with matching `index` values in submission order.

### `GET /v1/models`

Lists models available in the local Ollama instance. Proxied from Ollama's `/v1/models`.

**Response (`200`)**

```json
{
  "object": "list",
  "data": [
    {"id": "qwen3-embedding:0.6b", "object": "model", "owned_by": "ollama"}
  ]
}
```

### `GET /health`

Liveness/readiness probe. Does not require an API key. Returns `200` if FastAPI is up and Ollama responds; `503` otherwise.

```json
{"status": "ok", "ollama": "reachable"}
```

### Error responses

| Status | Body                                       | When                                            |
| ------ | ------------------------------------------ | ----------------------------------------------- |
| `401`  | `{"error": "unauthorized"}`                | Missing or invalid `Authorization` header.      |
| `404`  | `{"error": "not found"}`                   | Unknown route.                                  |
| `422`  | FastAPI validation error                   | Malformed request body.                         |
| `502`  | `{"error": "upstream unavailable"}`        | Ollama unreachable or returned a 5xx.           |
| `503`  | `{"status": "degraded", "ollama": "down"}` | `/health` only, when Ollama is down.            |

## API Usage (end-user perspective)

```bash
curl https://qwen-embed.yourdomain.com/v1/embeddings \
  -H "Authorization: Bearer $QWEN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-embedding:0.6b",
    "input": ["hello world", "the quick brown fox"]
  }'
```

Compatible with any OpenAI SDK by setting `base_url` and `api_key`:

```python
from openai import OpenAI
c = OpenAI(base_url="https://qwen-embed.yourdomain.com/v1", api_key="...")
v = c.embeddings.create(model="qwen3-embedding:0.6b", input="hello").data[0].embedding
```

## Performance Expectations (Mac Mini M4, 16 GB RAM, native Ollama)

| Metric                        | Estimate                            |
| ----------------------------- | ----------------------------------- |
| Model size on disk            | ~640 MB                             |
| Model memory (loaded)         | ~1 GB                               |
| Embedding dimension           | 1024                                |
| Single-text latency (short)   | ~10–30 ms                           |
| Throughput (batched, GPU hot) | ~1–5k tokens/s                      |
| Concurrent requests           | Many (embedding is ~stateless and fast); practical cap set by Ollama's queue |

## Security Considerations

- API key stored in `.env`, injected into the FastAPI container as `QWEN_API_KEY` by Docker Compose's variable substitution. Never baked into the image.
- API key comparison uses `secrets.compare_digest` to avoid timing attacks.
- The FastAPI container's `8080` port is bound to `127.0.0.1` on the host — public access only goes through Cloudflare Tunnel.
- Ollama on the host listens on loopback by default; Docker Desktop's `host.docker.internal` mapping reaches it without exposing it to the LAN.
- Cloudflare Tunnel means no open ports on the router.
- Future: per-key rate limits via FastAPI middleware (e.g., `slowapi`) keyed on the API key, and structured access logs.

## File Structure

```
deploy-qwen-locally/
├── technical-design.md
├── .env.example              # QWEN_API_KEY=your-secret-key
├── docker-compose.yml        # api + cloudflared services
├── Dockerfile                # FastAPI image
├── requirements.txt          # fastapi, uvicorn, httpx, pydantic
└── app/
    ├── __init__.py
    └── main.py               # FastAPI app: auth + proxy to Ollama
```

## Implementation Steps

1. **Host setup**: install Ollama natively (`brew install ollama`), run `ollama serve`, `ollama pull qwen3-embedding:0.6b`. Verify with `curl http://127.0.0.1:11434/v1/models`.
2. **Generate API key**: `openssl rand -hex 32` → write to `.env` as `QWEN_API_KEY=...`.
3. **FastAPI app**: implement `app/main.py` (auth + `/v1/embeddings` + `/v1/models` + `/health`).
4. **Dockerfile + requirements.txt**: build image, run locally with `docker compose up api`.
5. **Smoke test from inside the container**: `curl -H "Authorization: Bearer $KEY" http://localhost:8080/v1/embeddings -d '{...}'` and confirm a 1024-dim vector comes back.
6. **Wire up Cloudflare Tunnel**: add `cloudflared` service to compose; `docker compose up` and grab the `*.trycloudflare.com` URL from logs (or configure a stable subdomain).
7. **External smoke test**: same curl against the public URL.
