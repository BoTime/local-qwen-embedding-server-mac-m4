"""Minimal mock of Ollama's OpenAI-compatible API for CI smoke tests.

Runs on 0.0.0.0:11434 by default so a sibling Docker container can reach it
through the bridge gateway. Returns deterministic 1024-dim float vectors.
"""

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class EmbedReq(BaseModel):
    model: str
    input: str | list[str]
    encoding_format: str | None = None


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "qwen3-embedding:0.6b", "object": "model", "owned_by": "ollama"}
        ],
    }


@app.post("/v1/embeddings")
def embed(req: EmbedReq):
    inputs = req.input if isinstance(req.input, list) else [req.input]
    data = [
        {"object": "embedding", "index": i, "embedding": [0.001] * 1024}
        for i in range(len(inputs))
    ]
    return {
        "object": "list",
        "data": data,
        "model": req.model,
        "usage": {"prompt_tokens": 8, "total_tokens": 8},
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=11434)
