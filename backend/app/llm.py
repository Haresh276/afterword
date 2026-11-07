"""LLM access through any OpenAI-compatible endpoint.

Defaults:
  OPENAI_API_KEY set           -> OpenAI, model gpt-4.1-mini
  GEMINI_API_KEY set           -> Google Gemini (free tier) via its OpenAI-compatible endpoint
  LLM_BASE_URL / LLM_MODEL     -> override for anything else
  nothing set                  -> offline heuristics (agents fall back; demo still runs)
"""
from __future__ import annotations

import json
import os
import re

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _config() -> tuple[str | None, str | None, str]:
    if os.getenv("LLM_API_KEY"):
        return os.getenv("LLM_API_KEY"), os.getenv("LLM_BASE_URL"), os.getenv("LLM_MODEL", "gpt-4.1-mini")
    if os.getenv("OPENAI_API_KEY"):
        return os.getenv("OPENAI_API_KEY"), os.getenv("LLM_BASE_URL"), os.getenv("LLM_MODEL", "gpt-4.1-mini")
    if os.getenv("GEMINI_API_KEY"):
        return os.getenv("GEMINI_API_KEY"), GEMINI_URL, os.getenv("LLM_MODEL") or _working.get("gemini") or GEMINI_MODELS[0]
    return None, None, "offline-heuristic"


# Gemini 2.5 was deprecated on 2 Nov 2026 (traffic reroutes on 1 Dec) and new keys may not reach it at all,
# so try current models first and remember the first one this key can actually use.
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash"]
_working: dict[str, str] = {}


def _create(**kw):
    """chat.completions.create with model fallback for Gemini; the chosen model sticks for the process."""
    key, base, model = _config()
    if base != GEMINI_URL or os.getenv("LLM_MODEL") or "gemini" in _working:
        return client().chat.completions.create(model=model, **kw)
    last: Exception | None = None
    for m in GEMINI_MODELS:
        try:
            r = client().chat.completions.create(model=m, **kw)
            _working["gemini"] = m
            return r
        except Exception as exc:  # noqa: BLE001  (404 model not found / 403 not allowed -> next model)
            last = exc
            code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
            if code not in (400, 403, 404):
                raise
    raise last  # type: ignore[misc]


def available() -> bool:
    return _config()[0] is not None


def model_name() -> str:
    return _config()[2]


def client():
    from openai import OpenAI

    key, base, _ = _config()
    # A judge is waiting on every call: fail fast and let the offline fallbacks take over.
    kw = {"api_key": key, "timeout": float(os.getenv("LLM_TIMEOUT", "25")), "max_retries": 1}
    return OpenAI(base_url=base, **kw) if base else OpenAI(**kw)


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


def json_call(system: str, user: str, temperature: float = 0.1) -> dict:
    key, _, model = _config()
    if not key:
        raise RuntimeError("no LLM configured")
    resp = _create(
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return _extract_json(resp.choices[0].message.content or "{}")


def json_call_image(system: str, user: str, image_bytes: bytes, mime: str = "image/png") -> dict:
    """Multimodal JSON call (Gemini and OpenAI both accept data-URL images on the chat endpoint)."""
    import base64

    key, _, model = _config()
    if not key:
        raise RuntimeError("no LLM configured")
    url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    resp = _create(
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": [{"type": "text", "text": user},
                                               {"type": "image_url", "image_url": {"url": url}}]}],
    )
    return _extract_json(resp.choices[0].message.content or "{}")


def text_call(system: str, user: str, temperature: float = 0.4) -> str:
    key, _, model = _config()
    if not key:
        raise RuntimeError("no LLM configured")
    resp = _create(
        temperature=temperature,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return (resp.choices[0].message.content or "").strip()
