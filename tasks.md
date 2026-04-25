# Implementation Tasks

Task breakdown for [technical-design.md](technical-design.md). Each task lists a **Goal**, **Acceptance criteria**, and a **Verify** step (an exact command + expected output where possible). Check items off as you complete them.

**Conventions**
- `[ ]` = todo, `[x]` = done.
- "On host" = run on the Mac Mini M4 directly (terminal on macOS).
- "In container" = run inside the FastAPI container (`docker compose exec api …`) or against it from the host.
- Pre-checked items are scaffolding already complete in this repo; verify they still match the design before assuming done.

---

## Phase 1 — Repository scaffolding

### 1.1 [x] FastAPI app skeleton

**Goal.** A minimal FastAPI app that boots, validates `QWEN_API_KEY`, exposes `/v1/embeddings`, `/v1/models`, `/health`.

**Acceptance criteria**
- [app/main.py](app/main.py) exists with the three routes, Pydantic `EmbeddingsRequest` schema, `secrets.compare_digest` auth, and an `httpx.AsyncClient` in a `lifespan` context manager.
- `MODEL` constant is `qwen3-embedding:0.6b`.
- `OLLAMA_BASE_URL` defaults to `http://host.docker.internal:11434` and is overridable via env.
- `[app/__init__.py](app/__init__.py)` exists (so `app` is a package).

**Verify**
```bash
grep -E '^(def|async def|@app\.)' app/main.py
# Expect routes: /v1/embeddings, /v1/models, /health
python -c "import ast; ast.parse(open('app/main.py').read())"   # syntax OK
```

### 1.2 [x] Dockerfile

**Goal.** Build a small image that runs the FastAPI app on port 8080.

**Acceptance criteria**
- Base `python:3.11-slim`.
- Installs from `requirements.txt`.
- Copies `app/` into the image.
- `CMD` runs `uvicorn app.main:app --host 0.0.0.0 --port 8080`.

**Verify**
```bash
docker build -t qwen-embed-api:dev .
# Build succeeds. Image size should be a few hundred MB, not multi-GB.
docker images qwen-embed-api:dev
```

### 1.3 [x] `requirements.txt`

**Goal.** Pinned dependencies.

**Acceptance criteria**
- Contains `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic`, all with exact pins.
- No `python-dotenv` (Compose injects env vars directly).

**Verify**
```bash
cat requirements.txt
# Expect 4 pinned lines.
```

### 1.4 [x] `docker-compose.yml`

**Goal.** Three services: `api` (FastAPI), `cloudflared` (tunnel), `print-url` (helper).

**Acceptance criteria**
- `api` builds from local Dockerfile, sets `QWEN_API_KEY` and `OLLAMA_BASE_URL`, has `extra_hosts: host.docker.internal:host-gateway`, binds `127.0.0.1:8080:8080`.
- `cloudflared` runs `tunnel --url http://api:8080` and `depends_on: api`.
- No `ollama` service (it runs on the host).
- No `nginx` service.

**Verify**
```bash
docker compose config         # parses without error
docker compose config --services
# Expect: api, cloudflared, print-url
```

### 1.5 [x] `.env.example` and `.gitignore`

**Goal.** Document required env vars; keep real `.env` out of git.

**Acceptance criteria**
- `.env.example` contains `QWEN_API_KEY=change-me-to-a-secret-key` and nothing sensitive.
- `.gitignore` includes `.env`.

**Verify**
```bash
grep -E '^QWEN_API_KEY=' .env.example
grep -Fxq '.env' .gitignore && echo OK
```

### 1.6 [x] README quick start

**Goal.** A reader can go from clone → working API in under 10 minutes by following the README.

**Acceptance criteria**
- Lists host-side steps (install Ollama, `ollama pull qwen3-embedding:0.6b`).
- Lists container-side steps (`cp .env.example .env`, edit, `docker compose up -d --build`).
- Shows a curl call against `/v1/embeddings` with `qwen3-embedding:0.6b`.

**Verify**
- Skim [README.md](README.md); make sure no curl example references `qwen3.5:*` or `/v1/chat/completions`.

---

## Phase 2 — Host setup (Mac Mini M4)

### 2.1 [x] Install Ollama natively on the host

**Goal.** Ollama running as a native macOS process so it has Metal GPU access.

**Acceptance criteria**
- `ollama` is on `$PATH`.
- `ollama serve` is running (Activity Monitor or `pgrep -fl 'ollama serve'`).
- Listening on `127.0.0.1:11434`.

**Verify** (on host)
```bash
brew install ollama          # if not already
ollama serve &               # leave running; or use brew services
curl -fsS http://127.0.0.1:11434/api/version
# Expect JSON: {"version":"..."}
```

### 2.2 [x] Pull the embedding model

**Goal.** `qwen3-embedding:0.6b` available locally.

**Acceptance criteria**
- `ollama list` shows `qwen3-embedding:0.6b` with non-zero size.
- A direct embed call returns a vector.

**Verify** (on host)
```bash
ollama pull qwen3-embedding:0.6b
ollama list | grep qwen3-embedding
curl -fsS http://127.0.0.1:11434/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-embedding:0.6b","input":"hello"}' \
  | python -c "import json,sys; d=json.load(sys.stdin); print(len(d['data'][0]['embedding']))"
# Expect: 1024
```

### 2.3 [x] Confirm `host.docker.internal` reachability

**Goal.** A container can reach Ollama on the host.

**Acceptance criteria**
- From inside an ad-hoc container, `host.docker.internal:11434` responds.

**Verify** (on host)
```bash
docker run --rm --add-host=host.docker.internal:host-gateway curlimages/curl:8.10.1 \
  -fsS http://host.docker.internal:11434/api/version
# Expect: {"version":"..."}
```

If this fails, set `OLLAMA_HOST=0.0.0.0:11434` and restart `ollama serve`.

---

## Phase 3 — Local containerized run

### 3.1 [x] Generate and store API key

**Goal.** A strong `QWEN_API_KEY` in `.env`.

**Acceptance criteria**
- `.env` exists, is git-ignored, and `QWEN_API_KEY` is at least 32 hex chars (not the placeholder).

**Verify** (on host)
```bash
cp -n .env.example .env
KEY=$(openssl rand -hex 32)
# edit .env so it has: QWEN_API_KEY=$KEY
grep -E '^QWEN_API_KEY=[a-f0-9]{64}$' .env && echo OK
git check-ignore -v .env       # confirms .env is ignored
```

### 3.2 [x] Build and start the API container

**Goal.** `docker compose up` brings the API up cleanly.

**Acceptance criteria**
- `docker compose ps` shows `api` as `running` / `healthy` (or at least `running`; we don't define a healthcheck on the api service yet).
- No restart loop in `docker logs qwen-embed-api`.

**Verify** (on host)
```bash
docker compose up -d --build api
sleep 3
docker compose ps api
docker logs qwen-embed-api --tail 20
# Expect "Uvicorn running on http://0.0.0.0:8080" and no tracebacks.
```

### 3.3 [x] `/health` returns 200 with Ollama reachable

**Goal.** Confirm the container can reach Ollama on the host through `host.docker.internal`.

**Acceptance criteria**
- `GET /health` returns HTTP 200 and JSON `{"status": "ok", "ollama": "reachable"}`.
- Returns 503 if Ollama is stopped (regression sanity check, optional).

**Verify** (on host)
```bash
curl -fsS http://127.0.0.1:8080/health
# Expect: {"status":"ok","ollama":"reachable"}
```

### 3.4 [x] Auth: missing/wrong key returns 401

**Goal.** `/v1/*` rejects unauthenticated requests.

**Acceptance criteria**
- No header → 401.
- Wrong bearer → 401.
- Correct bearer → not 401.

**Verify** (on host)
```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/v1/models           # 401
curl -s -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer wrong' http://127.0.0.1:8080/v1/models  # 401
. ./.env
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $QWEN_API_KEY" http://127.0.0.1:8080/v1/models  # 200
```

### 3.5 [x] `POST /v1/embeddings` returns a 1024-dim vector

**Goal.** End-to-end embedding works through FastAPI → Ollama.

**Acceptance criteria**
- Single-string input returns one embedding of length 1024.
- Array input of N strings returns N embeddings with `index` 0..N-1.
- `model` in response equals `qwen3-embedding:0.6b`.

**Verify** (on host)
```bash
. ./.env
# single
curl -fsS http://127.0.0.1:8080/v1/embeddings \
  -H "Authorization: Bearer $QWEN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-embedding:0.6b","input":"hello world"}' \
  | python -c "import json,sys; d=json.load(sys.stdin); assert len(d['data'])==1 and len(d['data'][0]['embedding'])==1024; print('OK', d['model'])"

# batch
curl -fsS http://127.0.0.1:8080/v1/embeddings \
  -H "Authorization: Bearer $QWEN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-embedding:0.6b","input":["a","b","c"]}' \
  | python -c "import json,sys; d=json.load(sys.stdin); assert len(d['data'])==3 and [x['index'] for x in d['data']]==[0,1,2]; print('OK')"
```

### 3.6 [x] `GET /v1/models` lists `qwen3-embedding:0.6b`

**Goal.** Model listing endpoint works.

**Acceptance criteria**
- 200 with a JSON body whose `data[*].id` includes `qwen3-embedding:0.6b`.

**Verify** (on host)
```bash
. ./.env
curl -fsS -H "Authorization: Bearer $QWEN_API_KEY" http://127.0.0.1:8080/v1/models \
  | python -c "import json,sys; d=json.load(sys.stdin); ids=[m['id'] for m in d['data']]; assert 'qwen3-embedding:0.6b' in ids; print(ids)"
```

### 3.7 [x] Bad request body returns 422

**Goal.** Pydantic validation surfaces malformed input clearly.

**Acceptance criteria**
- POST without `model` or without `input` returns 422.

**Verify** (on host)
```bash
. ./.env
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $QWEN_API_KEY" \
  -H 'Content-Type: application/json' -d '{}' \
  http://127.0.0.1:8080/v1/embeddings
# Expect: 422
```

### 3.8 [x] Upstream-down behavior returns 502

**Goal.** When Ollama is unreachable, the API returns 502 (not 500/timeout).

**Acceptance criteria**
- Stopping `ollama serve` → next `/v1/embeddings` call returns 502 with `{"error": "upstream unavailable"}`.

**Verify** (on host)
```bash
# Stop Ollama temporarily.
pkill -f 'ollama serve' || true
sleep 2
. ./.env
curl -s -o /tmp/r.json -w '%{http_code}\n' \
  -H "Authorization: Bearer $QWEN_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-embedding:0.6b","input":"x"}' \
  http://127.0.0.1:8080/v1/embeddings
cat /tmp/r.json
# Expect status 502 and body {"error":"upstream unavailable"}.
# Restart: ollama serve &
```

---

## Phase 4 — Public exposure via Cloudflare Tunnel

### 4.1 [x] Quick tunnel comes up

**Goal.** `cloudflared` prints a `*.trycloudflare.com` URL.

**Acceptance criteria**
- `docker compose up -d cloudflared` starts the container without errors.
- `docker logs cloudflared` contains a `https://….trycloudflare.com` URL within ~30 seconds.

**Verify** (on host)
```bash
docker compose up -d cloudflared
docker logs print-url 2>&1 | grep trycloudflare.com
# Expect a line like: Cloudflare Tunnel URL: https://<random>.trycloudflare.com
```

### 4.2 [x] Public URL serves /health (no auth)

**Goal.** External requests reach the FastAPI container.

**Acceptance criteria**
- `curl https://<tunnel>/health` returns 200 with `{"status": "ok", ...}`.

**Verify** (from any internet host)
```bash
URL=$(docker logs cloudflared 2>&1 | grep -oE 'https://[^ ]+trycloudflare.com' | head -1)
curl -fsS "$URL/health"
```

### 4.3 [x] Public URL serves /v1/embeddings with API key

**Goal.** End-to-end public flow works.

**Acceptance criteria**
- POST against `$URL/v1/embeddings` with `Authorization: Bearer $QWEN_API_KEY` returns a 1024-dim vector.
- The same call without the header returns 401.

**Verify**
```bash
. ./.env
URL=$(docker logs cloudflared 2>&1 | grep -oE 'https://[^ ]+trycloudflare.com' | head -1)
curl -s -o /dev/null -w '%{http_code}\n' "$URL/v1/embeddings"   # 401
curl -fsS "$URL/v1/embeddings" \
  -H "Authorization: Bearer $QWEN_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-embedding:0.6b","input":"hello"}' \
  | python -c "import json,sys; d=json.load(sys.stdin); print(len(d['data'][0]['embedding']))"
# Expect: 1024
```

### 4.4 [x] Stable subdomain via authenticated tunnel

**Goal.** Replace the rotating `*.trycloudflare.com` URL with `qwen-embed.<your-domain>`.

**Acceptance criteria**
- One-time `cloudflared tunnel login`, `tunnel create qwen-embed`, `tunnel route dns …` done on host.
- Tunnel credentials JSON mounted into the cloudflared container (read-only).
- `compose up` brings up the same hostname every time.

**Verify**
```bash
URL=https://qwen-embed.<your-domain>
curl -fsS "$URL/health"
```

---

## Phase 5 — End-to-end verification

### 5.1 [x] OpenAI-SDK compatibility check

**Goal.** Standard OpenAI SDK can call the service unchanged.

**Acceptance criteria**
- A short Python snippet using `openai` returns a 1024-dim vector.

**Verify** (expect `OK 1024`)
```bash
# local
scripts/sdk_smoke.sh

# or against the public URL
BASE_URL=https://qwen-embed.your-domain.com/v1 scripts/sdk_smoke.sh
```

The shell wrapper [scripts/sdk_smoke.sh](scripts/sdk_smoke.sh) loads `.env`, ensures the `openai` SDK is installed, then runs [scripts/sdk_smoke.py](scripts/sdk_smoke.py) (which does the actual API call and dim check). Override target via `BASE_URL`, `MODEL`, `INPUT_TEXT`, `EXPECTED_DIM` env vars; the Python script can also be invoked directly without the shell wrapper.

### 5.2 [x] Restart resilience

**Goal.** `docker compose down && docker compose up -d` returns the system to a working state without manual fixup.

**Acceptance criteria**
- After a full stop/start cycle, `/health` is 200 and `/v1/embeddings` works within ~10 s.

**Verify**
```bash
docker compose down
docker compose up -d --build
sleep 5
curl -fsS http://127.0.0.1:8080/health
```

---

## Phase 6 — Stretch / future work (not required for v1)

These come from the design doc's "future" hints; track them here so they don't get forgotten. Each should become its own concrete task before being worked on.

- [x] **Per-key rate limits.** Add a middleware (e.g. `slowapi`) keyed on the API key. Acceptance: 100 RPS burst → 429 after configured threshold.
  - Implemented with `slowapi`. Limit comes from `RATE_LIMIT` env (default `100/minute`). Bucket key is `key:<label>` for authenticated requests, `ip:<client>` otherwise. 429 returns `{"error": "rate limit exceeded"}`.
- [x] **Multiple API keys with labels.** Replace `QWEN_API_KEY` (single value) with a `QWEN_API_KEYS` map (`label1=key1,label2=key2`); log requests with the matched label.
  - `QWEN_API_KEYS` (CSV of `label=key`) takes precedence; falls back to `QWEN_API_KEY` (treated as label `default`). Matched label is recorded in the JSON access log as `key_label`.
- [x] **Structured access logs.** Switch uvicorn to JSON access logs; include matched key label, model, input length, latency.
  - HTTP middleware emits one JSON line per request (`ts`, `method`, `path`, `status`, `duration_ms`, `key_label`, `model`, `input_length`). Uvicorn's plain access log is disabled via `--no-access-log` in the Dockerfile.
- [x] **Healthcheck in compose.** Add a `healthcheck` block on the `api` service hitting `/health`, so `cloudflared` `depends_on: condition: service_healthy`.
  - Healthcheck uses `python -c "urllib.request.urlopen('/health')"` (no curl needed in the slim image). `cloudflared` now waits on `condition: service_healthy`.
- [x] **CI smoke test.** GitHub Actions workflow that runs `docker compose up -d`, waits for `/health`, runs the curl + SDK checks from §5.1, tears down. (Skips Ollama: stub or use `OLLAMA_BASE_URL` pointed at a mock.)
  - `.github/workflows/smoke.yml`. Mock Ollama (`tests/mock_ollama.py`, FastAPI app, returns deterministic 1024-dim vectors) runs on the runner host; the API container reaches it via `host.docker.internal:host-gateway`. Workflow exercises `/health`, missing/wrong key 401s, `/v1/models` listing, single + batch embeddings, malformed-body 422, and the OpenAI SDK call from §5.1.
- [x] **Model swap parameterization.** Make `MODEL` an env var (`EMBEDDING_MODEL`) instead of a hardcoded constant in `app/main.py`, defaulting to `qwen3-embedding:0.6b`.
  - `EMBEDDING_MODEL` env var, surfaced through `docker-compose.yml` and `.env.example`. `MODEL` kept as an alias for backwards compatibility.
