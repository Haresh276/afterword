"""LLM access. Callers speak Chat Completions; Gemini is reached through its native API (gemini_native.py).

Defaults:
  OPENAI_API_KEY set           -> OpenAI, model gpt-4.1-mini
  GEMINI_API_KEY set           -> Google Gemini, native generateContent, newest flash model the key can use
  LLM_BASE_URL / LLM_MODEL     -> override for anything else
  nothing set                  -> offline heuristics (agents fall back; demo still runs)
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

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

# A judge is waiting on every call. If the model is slow or down, stop asking for a while and let the
# offline paths answer instantly, instead of making every step wait out a timeout.
COOLDOWN = float(os.getenv("LLM_COOLDOWN", "300"))
_state = {"down_until": 0.0, "last_error": "", "last_ok": "", "last_ms": 0, "probed": False}
_state_lock = threading.Lock()


def _status_code(exc: Exception):
    return getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)


def _effort(model: str) -> dict:
    """Gemini 3.x thinks at 'medium' by default; 'low' is much faster and plenty for these short tasks."""
    e = os.getenv("LLM_REASONING_EFFORT", "low")
    return {"reasoning_effort": e} if e and e != "default" and model.startswith("gemini-3") else {}


def _gemini(model: str, timeout: float | None, kw: dict):
    from . import gemini_native
    return gemini_native.create(_config()[0], model, timeout or float(os.getenv("LLM_TIMEOUT", "30")), **kw)


def _call(model: str, timeout: float | None, kw: dict):
    if _config()[1] == GEMINI_URL and os.getenv("LLM_GEMINI_COMPAT") != "1":
        return _gemini(model, timeout, kw)
    c = client(timeout)
    extra = _effort(model)
    try:
        return c.chat.completions.create(model=model, **extra, **kw)
    except Exception as exc:  # noqa: BLE001
        if extra and _status_code(exc) == 400:   # this model/endpoint rejects reasoning_effort: retry plain
            return c.chat.completions.create(model=model, **kw)
        raise


def healthy() -> bool:
    return time.time() >= _state["down_until"]


def _mark(ok: bool, model: str = "", err: Exception | None = None, ms: int = 0):
    with _state_lock:
        if ok:
            _state.update(down_until=0.0, last_ok=model, last_ms=ms, last_error="")
        else:
            _state.update(down_until=time.time() + COOLDOWN, last_error=f"{type(err).__name__}: {str(err)[:200]}")


def _create(timeout: float | None = None, **kw):
    """chat.completions.create with model fallback for Gemini; the chosen model sticks for the process.
    Raises immediately while the model is cooling down, so callers fall back to their offline path."""
    if not healthy():
        raise RuntimeError("model temporarily unavailable (cooling down after a failure)")
    key, base, model = _config()
    t0 = time.time()
    if base != GEMINI_URL or os.getenv("LLM_MODEL") or "gemini" in _working:
        try:
            r = _call(model, timeout, kw)
        except Exception as exc:  # noqa: BLE001
            if _status_code(exc) not in (400, 413, 422):   # bad request = our input; anything else = model trouble
                _mark(False, err=exc)
            raise
        _mark(True, model, ms=int((time.time() - t0) * 1000))
        return r
    if _state["probed"]:   # the startup probe found no usable model; the background re-probe will retry
        raise RuntimeError("no Gemini model is answering right now")
    last: Exception | None = None
    for m in GEMINI_MODELS:
        try:
            r = _call(m, timeout, kw)
            _working["gemini"] = m
            _mark(True, m, ms=int((time.time() - t0) * 1000))
            return r
        except Exception as exc:  # noqa: BLE001  (not found / not allowed / slow / overloaded -> next model)
            last = exc
            if _status_code(exc) in (413, 422):
                raise
    _mark(False, err=last)
    raise last  # type: ignore[misc]


def probe() -> dict:
    """Try each candidate model once with a tiny prompt. Used at startup (so the first visitor never waits on
    a dead model) and by /api/llm/ping. Never returns the key."""
    key, base, current = _config()
    if not key:
        return {"available": False}
    out = []
    models = [current]
    if base == GEMINI_URL and not os.getenv("LLM_MODEL"):
        models = list(GEMINI_MODELS)
        try:                                   # ask Google which models this key can use right now
            from . import gemini_native
            listed = gemini_native.list_models(key)
            models = listed[:3] + [m for m in GEMINI_MODELS if m in listed and m not in listed[:3]] or models
            out.append({"model": "(list models)", "ok": True, "ms": 0, "reply": ", ".join(listed[:6])})
        except Exception as exc:  # noqa: BLE001
            out.append({"model": "(list models)", "ok": False, "ms": 0, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
    for m in models:
        t0 = time.time()
        try:
            r = _call(m, 20.0, {"messages": [{"role": "user", "content": "Reply with the single word: ok"}]})
            ms = int((time.time() - t0) * 1000)
            out.append({"model": m, "ok": True, "ms": ms, "reply": (r.choices[0].message.content or "")[:20]})
            if base == GEMINI_URL and not os.getenv("LLM_MODEL"):
                _working["gemini"] = m
            _mark(True, m, ms=ms)
            break
        except Exception as exc:  # noqa: BLE001
            out.append({"model": m, "ok": False, "ms": int((time.time() - t0) * 1000),
                        "error": f"{type(exc).__name__} {_status_code(exc) or ''}: {str(exc)[:160]}".replace(key, "***")})
    _state["probed"] = True
    if not any(o["ok"] and not o["model"].startswith("(") for o in out):
        _mark(False, err=RuntimeError(next((o["error"] for o in reversed(out) if not o["ok"]), "no models")))
    return {"available": True, "chosen": _working.get("gemini", current), "tries": out, **state()}


def time_one(model: str, effort: str = "") -> dict:
    if not re.fullmatch(r"[a-z0-9.\-]+", model) or not available():
        return {"ok": False, "error": "bad model name or no key"}
    old = os.environ.get("LLM_REASONING_EFFORT")
    t0 = time.time()
    try:
        if effort:
            os.environ["LLM_REASONING_EFFORT"] = effort
        r = _call(model, 25.0, {"messages": [{"role": "user", "content": "Reply with the single word: ok"}]})
        return {"model": model, "effort": effort or "default", "ok": True, "ms": int((time.time() - t0) * 1000),
                "reply": (r.choices[0].message.content or "")[:20]}
    except Exception as exc:  # noqa: BLE001
        return {"model": model, "effort": effort or "default", "ok": False, "ms": int((time.time() - t0) * 1000),
                "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    finally:
        if effort:
            if old is None:
                os.environ.pop("LLM_REASONING_EFFORT", None)
            else:
                os.environ["LLM_REASONING_EFFORT"] = old


def keep_probing(stop: "threading.Event") -> None:
    """Background loop: probe once at startup, then re-probe whenever the model is down, so a slow or
    failing model never makes a visitor wait and a recovered one is picked up again within COOLDOWN."""
    if not available():
        return
    while not stop.is_set():
        if not healthy() or not _state["probed"]:
            try:
                probe()
            except Exception:  # noqa: BLE001
                pass
        stop.wait(COOLDOWN if not healthy() else 60)


def state() -> dict:
    left = max(0, int(_state["down_until"] - time.time()))
    return {"healthy": left == 0, "cooldown_seconds_left": left, "last_ok_model": _state["last_ok"],
            "last_ms": _state["last_ms"], "last_error": _state["last_error"]}


def available() -> bool:
    return _config()[0] is not None


def model_name() -> str:
    return _config()[2]


def client(timeout: float | None = None):
    from openai import OpenAI

    key, base, _ = _config()
    kw = {"api_key": key, "timeout": timeout or float(os.getenv("LLM_TIMEOUT", "30")), "max_retries": 0}
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


def json_call_image(system: str, user: str, image_bytes: bytes, mime: str = "image/png", timeout: float | None = None) -> dict:
    """Multimodal JSON call (Gemini and OpenAI both accept data-URL images on the chat endpoint)."""
    import base64

    key, _, model = _config()
    if not key:
        raise RuntimeError("no LLM configured")
    url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    resp = _create(
        timeout=timeout,
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
