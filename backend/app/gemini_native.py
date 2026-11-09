"""Gemini through its native generateContent API, returning OpenAI-shaped objects.

Why: on this deployment the OpenAI-compatibility endpoint left Gemini 3.x requests hanging for 20+ seconds,
while the native API answers in about a second with thinking set to "low". Everything above this module
(llm.py, the AG Studio shim) keeps speaking Chat Completions; this file translates both ways:

  messages (system / user text+images / assistant tool_calls / tool results)  ->  contents + systemInstruction
  tools + tool_choice                                                          ->  functionDeclarations + toolConfig
  response_format json_object / json_schema                                    ->  responseMimeType (+ responseSchema)
  candidates[0].content.parts (text, functionCall; thought parts dropped)       ->  choices[0].message

The key goes in the x-goog-api-key header, never in a URL, so it can't leak into logs.
"""
from __future__ import annotations

import json
import os
import re
import uuid

import httpx

BASE = "https://generativelanguage.googleapis.com/v1beta"
# Gemini 3 validates thought signatures on replayed function calls. AG Studio's adapter doesn't carry them,
# so replayed calls use the documented placeholder that skips validation for externally supplied history.
SKIP_SIGNATURE = "skip_thought_signature_validator"


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self) -> dict:
        def dump(v):
            if isinstance(v, _Obj):
                return v.model_dump()
            if isinstance(v, list):
                return [dump(x) for x in v]
            return v
        return {k: dump(v) for k, v in self.__dict__.items()}


class GeminiError(Exception):
    def __init__(self, status: int, message: str):
        self.status_code = status
        super().__init__(f"Gemini {status}: {message}")


def _headers(key: str) -> dict:
    return {"x-goog-api-key": key, "content-type": "application/json"}


def list_models(key: str, timeout: float = 10.0) -> list[str]:
    """Model ids this key can call generateContent on, best flash models first."""
    r = httpx.get(f"{BASE}/models", headers=_headers(key), params={"pageSize": 200}, timeout=timeout)
    if r.status_code != 200:
        raise GeminiError(r.status_code, r.text[:200])
    ids = [m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
           if "generateContent" in (m.get("supportedGenerationMethods") or [])]

    def rank(mid: str):
        v = re.match(r"gemini-(\d+(?:\.\d+)?)-flash(-lite)?$", mid)
        if v:
            return (0, -float(v.group(1)), bool(v.group(2)))    # newest plain flash first, then lite
        if mid in ("gemini-flash-latest", "gemini-flash-lite-latest"):
            return (1, 0.0, mid.endswith("lite-latest"))
        return (2, 0.0, False)
    return sorted([m for m in ids if rank(m)[0] < 2], key=rank)


def _parts(content) -> list[dict]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}] if content else []
    out = []
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") in ("text", "input_text", "output_text"):
            out.append({"text": c.get("text", "")})
        elif c.get("type") == "image_url":
            url = (c.get("image_url") or {}).get("url", "")
            m = re.match(r"data:([^;]+);base64,(.*)", url, re.S)
            if m:
                out.append({"inlineData": {"mimeType": m.group(1), "data": m.group(2)}})
    return out


def to_request(kw: dict, model: str) -> dict:
    system, contents, names = [], [], {}
    for msg in kw.get("messages") or []:
        role = msg.get("role")
        if role in ("system", "developer"):
            system += _parts(msg.get("content"))
        elif role == "user":
            contents.append({"role": "user", "parts": _parts(msg.get("content")) or [{"text": " "}]})
        elif role == "assistant":
            parts = _parts(msg.get("content"))
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                names[tc.get("id")] = fn.get("name")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                parts.append({"functionCall": {"name": fn.get("name"), "args": args}, "thoughtSignature": SKIP_SIGNATURE})
            contents.append({"role": "model", "parts": parts or [{"text": " "}]})
        elif role == "tool":
            raw = msg.get("content")
            try:
                resp = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                resp = raw
            if not isinstance(resp, dict):
                resp = {"result": resp}
            part = {"functionResponse": {"name": names.get(msg.get("tool_call_id"), "tool"), "response": resp}}
            if contents and contents[-1]["role"] == "user" and all("functionResponse" in p for p in contents[-1]["parts"]):
                contents[-1]["parts"].append(part)     # parallel tool results go back in one turn
            else:
                contents.append({"role": "user", "parts": [part]})
    gen: dict = {}
    if kw.get("temperature") is not None:
        gen["temperature"] = kw["temperature"]
    if kw.get("max_tokens"):
        gen["maxOutputTokens"] = kw["max_tokens"]
    fmt = kw.get("response_format") or {}
    if fmt.get("type") == "json_object":
        gen["responseMimeType"] = "application/json"
    elif fmt.get("type") == "json_schema":
        gen["responseMimeType"] = "application/json"
        schema = (fmt.get("json_schema") or {}).get("schema")
        if schema:
            gen["responseSchema"] = schema
    level = kw.pop("_thinking", None) or os.getenv("LLM_REASONING_EFFORT", "minimal")
    if model.startswith("gemini-3") and level not in ("", "default"):
        gen["thinkingConfig"] = {"thinkingLevel": level}
    elif model.startswith("gemini-2.5-flash"):
        gen["thinkingConfig"] = {"thinkingBudget": 0}
    req: dict = {"contents": contents or [{"role": "user", "parts": [{"text": " "}]}]}
    if system:
        req["systemInstruction"] = {"parts": system}
    if gen:
        req["generationConfig"] = gen
    tools = [t["function"] for t in kw.get("tools") or [] if t.get("type") == "function"]
    if tools:
        req["tools"] = [{"functionDeclarations": [{"name": t["name"], "description": t.get("description", ""),
                                                    "parameters": t.get("parameters") or {"type": "object", "properties": {}}}
                                                   for t in tools]}]
        tc = kw.get("tool_choice")
        if isinstance(tc, dict) and (tc.get("function") or {}).get("name"):
            req["toolConfig"] = {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [tc["function"]["name"]]}}
        elif tc in ("none", "required", "auto"):
            req["toolConfig"] = {"functionCallingConfig": {"mode": {"none": "NONE", "required": "ANY", "auto": "AUTO"}[tc]}}
    return req


def from_response(data: dict) -> _Obj:
    cand = (data.get("candidates") or [{}])[0]
    text, calls = [], []
    for p in (cand.get("content") or {}).get("parts") or []:
        if p.get("thought"):
            continue
        if "text" in p:
            text.append(p["text"])
        elif "functionCall" in p:
            fc = p["functionCall"]
            calls.append(_Obj(id=f"call_{uuid.uuid4().hex[:16]}", type="function",
                              function=_Obj(name=fc.get("name"), arguments=json.dumps(fc.get("args") or {}))))
    msg = _Obj(role="assistant", content="".join(text) or None, tool_calls=calls or None)
    u = data.get("usageMetadata") or {}
    usage = _Obj(prompt_tokens=u.get("promptTokenCount", 0), completion_tokens=u.get("candidatesTokenCount", 0),
                 total_tokens=u.get("totalTokenCount", 0))
    return _Obj(choices=[_Obj(index=0, message=msg, finish_reason=cand.get("finishReason"))], usage=usage,
                model=data.get("modelVersion"))


def create(key: str, model: str, timeout: float, **kw) -> _Obj:
    r = httpx.post(f"{BASE}/models/{model}:generateContent", headers=_headers(key),
                   json=to_request(dict(kw), model), timeout=timeout)
    if r.status_code == 400 and "thinking level" in r.text.lower():      # e.g. a Pro-class model without "minimal"
        r = httpx.post(f"{BASE}/models/{model}:generateContent", headers=_headers(key),
                       json=to_request({**kw, "_thinking": "low"}, model), timeout=timeout)
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except ValueError:
            msg = r.text
        raise GeminiError(r.status_code, msg[:300])
    return from_response(r.json())
