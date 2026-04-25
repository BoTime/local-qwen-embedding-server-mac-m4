# Deploy Qwen Embeddings Locally

Run `qwen3-embedding:0.6b` on a Mac Mini M4 and expose it as an OpenAI-compatible embeddings API. Ollama runs natively on the host (for Metal GPU); FastAPI + Cloudflare Tunnel run in Docker.

See [technical-design.md](technical-design.md) for the full design.

## One-time setup

1. **Install and run Ollama on the host** (not in Docker — Metal GPU isn't available to Linux containers):
   ```bash
   brew install ollama
   OLLAMA_HOST=0.0.0.0:11434 ollama serve     # leave running; bind to all interfaces so the api container can reach it
   ollama pull qwen3-embedding:0.6b
   ```
2. **Configure secrets**:
   ```bash
   cp .env.example .env
   # Edit .env and set QWEN_API_KEY (e.g. `openssl rand -hex 32`)
   ```

   For multiple labeled keys, set `QWEN_API_KEYS=alice=key1,bob=key2` instead of `QWEN_API_KEY` (the matched label is recorded in access logs and used as the rate-limit bucket). Other optional knobs: `EMBEDDING_MODEL`, `RATE_LIMIT`, `OLLAMA_BASE_URL` — see [.env.example](.env.example).
3. **Set up the Cloudflare named tunnel** (gives you a stable `https://<your-host>` URL):
   ```bash
   brew install cloudflared
   cloudflared tunnel login                                          # browser auth
   cloudflared tunnel create qwen-embed                              # writes ~/.cloudflared/<UUID>.json
   cloudflared tunnel route dns qwen-embed qwen-embed.your-domain.com
   ```
   Then:
   ```bash
   cp cloudflared/config.yml.example cloudflared/config.yml
   # Edit cloudflared/config.yml: set hostname to qwen-embed.your-domain.com
   # Edit .env: set CLOUDFLARE_CREDS_FILE to the absolute path of your <UUID>.json
   ```

## Run

```bash
docker compose down && docker compose up -d --build
```

## Test

```bash
. ./.env

# Local
curl http://localhost:8080/v1/embeddings \
  -H "Authorization: Bearer $QWEN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-embedding:0.6b","input":"hello world"}'

# Health (no auth required)
curl http://localhost:8080/health

# Public (use the hostname you configured in cloudflared/config.yml)
curl https://qwen-embed.your-domain.com/v1/embeddings \
  -H "Authorization: Bearer $QWEN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-embedding:0.6b","input":["hello","world"]}'
```

## Logs

```bash
docker logs -f qwen-embed-api    # FastAPI requests
docker logs -f cloudflared       # tunnel status
```

## Stop

```bash
docker compose down
```
