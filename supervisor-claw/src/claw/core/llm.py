"""DeepSeek client (OpenAI-compatible) for quota extraction etc."""

from __future__ import annotations

import json
from functools import cache

from openai import OpenAI

from ..config import get_settings


@cache
def get_client() -> OpenAI:
    s = get_settings()
    if not s.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set in .env")
    return OpenAI(api_key=s.deepseek_api_key, base_url=s.deepseek_base_url)


def chat_json(system: str, user: str, *, model: str | None = None, max_tokens: int = 800) -> dict:
    """Single-turn chat with strict JSON object output. Returns parsed dict."""
    s = get_settings()
    client = get_client()
    resp = client.chat.completions.create(
        model=model or s.deepseek_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
        temperature=0.0,
    )
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)
