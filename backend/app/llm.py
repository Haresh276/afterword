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
        return os.getenv("GEMINI_API_KEY"), GEMINI_URL, os.getenv("LLM_MODEL", "gemini-2.5-flash")
    return None, None, "offline-heuristic"


def available() -> bool:
    return _config()[0] is not None


def model_name() -> str:
    return _config()[2]


def client():
    from openai import OpenAI

    key, base, _ = _config()
    return OpenAI(api_key=key, base_url=base) if base else OpenAI(api_key=key)


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
    resp = client().chat.completions.create(
        model=model,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return _extract_json(resp.choices[0].message.content or "{}")


def text_call(system: str, user: str, temperature: float = 0.4) -> str:
    key, _, model = _config()
    if not key:
        raise RuntimeError("no LLM configured")
    resp = client().chat.completions.create(
        model=model, temperature=temperature,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return (resp.choices[0].message.content or "").strip()
