import json
import logging
import os
import secrets
import sys
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
MODEL = EMBEDDING_MODEL
RATE_LIMIT = os.environ.get("RATE_LIMIT", "100/minute")


def _load_api_keys() -> dict[str, str]:
    """Return {label: key}. Prefers QWEN_API_KEYS (label1=key1,label2=key2);
    falls back to QWEN_API_KEY as label "default"."""
    raw = os.environ.get("QWEN_API_KEYS", "").strip()
    if raw:
        keys: dict[str, str] = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "=" not in pair:
                raise RuntimeError(f"QWEN_API_KEYS entry missing '=': {pair!r}")
            label, key = (s.strip() for s in pair.split("=", 1))
            if not label or not key:
                raise RuntimeError(f"QWEN_API_KEYS entry has empty label or key: {pair!r}")
            keys[label] = key
        if not keys:
            raise RuntimeError("QWEN_API_KEYS is set but contains no valid entries")
        return keys
    single = os.environ.get("QWEN_API_KEY", "").strip()
    if not single:
        raise RuntimeError("Set QWEN_API_KEYS (label=key,...) or QWEN_API_KEY")
    return {"default": single}


API_KEYS = _load_api_keys()


access_log = logging.getLogger("qwen_embed.access")
if not access_log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    access_log.addHandler(_h)
    access_log.setLevel(logging.INFO)
    access_log.propagate = False


def _match_label(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    presented = authorization.removeprefix("Bearer ")
    for label, key in API_KEYS.items():
        if secrets.compare_digest(presented, key):
            return label
    return None


def _rate_limit_key(request: Request) -> str:
    label = getattr(request.state, "api_key_label", None)
    if label:
        return f"key:{label}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


limiter = Limiter(key_func=_rate_limit_key, default_limits=[RATE_LIMIT])


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(base_url=OLLAMA_BASE_URL, timeout=30.0)
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"error": "rate limit exceeded"})


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    body = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=body)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    start = time.perf_counter()
    request.state.api_key_label = _match_label(request.headers.get("authorization"))
    request.state.model = None
    request.state.input_length = None
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        status = 500
        raise
    finally:
        record = {
            "ts": round(time.time(), 3),
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "key_label": request.state.api_key_label,
            "model": request.state.model,
            "input_length": request.state.input_length,
        }
        access_log.info(json.dumps(record))
    return response


class EmbeddingsRequest(BaseModel):
    model: str
    input: str | list[str]
    encoding_format: str | None = None


def require_key(request: Request) -> str:
    label = getattr(request.state, "api_key_label", None)
    if not label:
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})
    return label


@app.post("/v1/embeddings")
@limiter.limit(RATE_LIMIT)
async def embeddings(request: Request, req: EmbeddingsRequest):
    require_key(request)
    request.state.model = req.model
    request.state.input_length = (
        len(req.input) if isinstance(req.input, str) else sum(len(s) for s in req.input)
    )
    try:
        r = await request.app.state.client.post(
            "/v1/embeddings", json=req.model_dump(exclude_none=True)
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail={"error": "upstream unavailable"})
    if r.status_code >= 500:
        raise HTTPException(status_code=502, detail={"error": "upstream unavailable"})
    return JSONResponse(status_code=r.status_code, content=r.json())


@app.get("/v1/models")
@limiter.limit(RATE_LIMIT)
async def models(request: Request):
    require_key(request)
    try:
        r = await request.app.state.client.get("/v1/models")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail={"error": "upstream unavailable"})
    return JSONResponse(status_code=r.status_code, content=r.json())


@app.get("/health")
async def health(request: Request):
    try:
        r = await request.app.state.client.get("/v1/models", timeout=2.0)
        if r.status_code == 200:
            return {"status": "ok", "ollama": "reachable"}
    except httpx.HTTPError:
        pass
    return JSONResponse(status_code=503, content={"status": "degraded", "ollama": "down"})
