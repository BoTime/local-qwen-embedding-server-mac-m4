#!/usr/bin/env python3
"""OpenAI-SDK compatibility smoke test for the FastAPI embeddings service.

Calls /v1/embeddings using the official `openai` SDK; exits 0 only if the
returned vector has the expected dimension. Used by tasks.md §5.1.

Env:
  QWEN_API_KEY  required.
  BASE_URL      default http://127.0.0.1:8080/v1
  MODEL         default qwen3-embedding:0.6b
  INPUT_TEXT    default "hello"
  EXPECTED_DIM  default 1024
"""
import os
import sys

from openai import OpenAI


def main() -> int:
    key = os.environ.get("QWEN_API_KEY")
    if not key:
        print("error: QWEN_API_KEY not set", file=sys.stderr)
        return 2

    base_url = os.environ.get("BASE_URL", "http://127.0.0.1:8080/v1")
    model = os.environ.get("MODEL", "qwen3-embedding:0.6b")
    text = os.environ.get("INPUT_TEXT", "hello")
    expected_dim = int(os.environ.get("EXPECTED_DIM", "1024"))

    client = OpenAI(base_url=base_url, api_key=key)
    vec = client.embeddings.create(model=model, input=text).data[0].embedding
    dim = len(vec)
    if dim != expected_dim:
        print(f"FAIL: expected dim {expected_dim}, got {dim}", file=sys.stderr)
        return 1
    print(f"OK {dim}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
