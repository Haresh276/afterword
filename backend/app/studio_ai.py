"""A small OpenAI Responses-API shim, so AG Studio's own AI agent can run on Gemini (or any
OpenAI-compatible chat model) instead of needing a paid OpenAI key.

AG Studio's documented adapter (frontend/src/studio/openai-adapter.ts, unchanged) POSTs a Responses
request and reads a Responses SSE stream. Here we translate that request to Chat Completions, call
the configured model once, and replay the answer as the same SSE events: output_item.added ->
deltas -> output_item.done -> response.completed. Tool calls round-trip as function_call items, so
Studio's agent can still add widgets, filter, and summarise the report.

The key never reaches the browser, the model is fixed server-side, and output tokens are capped.
"""
from __future__ import annotations

import copy
import json
import time
import uuid

from fastapi.responses import JSONResponse, StreamingResponse

from . import llm

MAX_OUTPUT_TOKENS = 8000
_DROP = {"$schema", "$id", "$comment", "additionalProperties", "unevaluatedProperties", "patternProperties",
         "examples", "default", "strict", "$anchor", "dependentRequired", "dependentSchemas", "if", "then", "else", "not"}


# ------------------------------------------------------------------------------ schema
def sanitize(schema: dict, depth: int = 0, defs: dict | None = None) -> dict:
    """JSON Schema 2020-12 (with $defs/$ref, nullable type arrays) -> the subset Gemini accepts."""
    if not isinstance(schema, dict):
        return schema
    defs = {**(defs or {}), **schema.get("$defs", {}), **schema.get("definitions", {})}
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        if depth > 6 or name not in defs:
            return {"type": "object", "description": schema.get("description", f"{name} (see tool description)")}
        merged = {**copy.deepcopy(defs[name]), **{k: v for k, v in schema.items() if k != "$ref"}}
        return sanitize(merged, depth + 1, defs)
    out: dict = {}
    for k, v in schema.items():
        if k in _DROP or k in ("$defs", "definitions"):
            continue
        if k == "type" and isinstance(v, list):
            kinds = [t for t in v if t != "null"]
            out["type"] = kinds[0] if kinds else "string"
            if "null" in v:
                out["nullable"] = True
        elif k == "const":
            out["enum"] = [v]
        elif k == "properties" and isinstance(v, dict):
            out[k] = {p: sanitize(s, depth + 1, defs) for p, s in v.items()}
        elif k in ("items",) and isinstance(v, dict):
            out[k] = sanitize(v, depth + 1, defs)
        elif k in ("anyOf", "oneOf", "allOf") and isinstance(v, list):
            opts = [sanitize(s, depth + 1, defs) for s in v]
            real = [o for o in opts if o.get("type") != "null"]
            if len(real) == 1:                     # X | null -> nullable X
                out.update(real[0])
                out["nullable"] = True
            else:
                out["anyOf"] = real
        else:
            out[k] = v
    if "enum" in out and "type" not in out:
        out["type"] = "string"
    return out


# ------------------------------------------------------------------------------ request
def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") in ("input_text", "output_text", "text"))


def to_chat(body: dict) -> dict:
    msgs: list[dict] = []
    if body.get("instructions"):
        msgs.append({"role": "system", "content": body["instructions"]})
    for item in body.get("input") or []:
        t = item.get("type", "message")
        if t == "message" or "role" in item and t not in ("function_call", "function_call_output"):
            role = {"developer": "system"}.get(item.get("role"), item.get("role", "user"))
            msgs.append({"role": role, "content": _text(item.get("content"))})
        elif t == "function_call":
            call = {"id": item["call_id"], "type": "function", "function": {"name": item["name"], "arguments": item.get("arguments") or "{}"}}
            if msgs and msgs[-1]["role"] == "assistant" and msgs[-1].get("tool_calls") is not None:
                msgs[-1]["tool_calls"].append(call)
            else:
                msgs.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif t == "function_call_output":
            out = item.get("output")
            msgs.append({"role": "tool", "tool_call_id": item["call_id"], "content": out if isinstance(out, str) else json.dumps(out)})
        # reasoning items are not replayed
    req: dict = {"model": llm.model_name(), "messages": msgs, "max_tokens": MAX_OUTPUT_TOKENS}
    tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", "")[:1024],
                                               "parameters": sanitize(t.get("parameters") or {"type": "object", "properties": {}})}}
             for t in body.get("tools") or [] if t.get("type") == "function"]
    if tools:
        req["tools"] = tools
        tc = body.get("tool_choice")
        if isinstance(tc, dict) and tc.get("name"):
            req["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
        elif tc in ("auto", "none", "required"):
            req["tool_choice"] = tc
    fmt = ((body.get("text") or {}).get("format") or {})
    if fmt.get("type") == "json_schema":
        req["response_format"] = {"type": "json_schema", "json_schema": {"name": fmt.get("name", "output"), "schema": sanitize(fmt.get("schema") or {})}}
    return req


# ------------------------------------------------------------------------------ response
def to_events(choice_msg: dict, usage: dict | None, model: str) -> list[dict]:
    rid = f"resp_{uuid.uuid4().hex[:20]}"
    output, events = [], []
    text = choice_msg.get("content") or ""
    if text:
        mid = f"msg_{uuid.uuid4().hex[:20]}"
        item = {"type": "message", "id": mid, "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}]}
        events += [{"type": "response.output_item.added", "item": {**item, "status": "in_progress", "content": []}},
                   {"type": "response.output_text.delta", "item_id": mid, "delta": text},
                   {"type": "response.output_item.done", "item": item}]
        output.append(item)
    for call in choice_msg.get("tool_calls") or []:
        fid = f"fc_{uuid.uuid4().hex[:20]}"
        args = call["function"].get("arguments") or "{}"
        item = {"type": "function_call", "id": fid, "call_id": call.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                "name": call["function"]["name"], "arguments": args, "status": "completed"}
        events += [{"type": "response.output_item.added", "item": {**item, "arguments": "", "status": "in_progress"}},
                   {"type": "response.function_call_arguments.delta", "item_id": fid, "delta": args},
                   {"type": "response.output_item.done", "item": item}]
        output.append(item)
    u = usage or {}
    events.append({"type": "response.completed", "response": {
        "id": rid, "created_at": int(time.time()), "model": model, "status": "completed", "output": output,
        "usage": {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0),
                  "total_tokens": u.get("total_tokens", 0)}}})
    return events


def respond(body: dict):
    req = to_chat(body)
    try:
        r = llm.client().chat.completions.create(**req)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": {"message": f"Model call failed: {str(exc)[:400]}"}}, status_code=502)
    msg = r.choices[0].message.model_dump() if r.choices else {"content": ""}
    events = to_events(msg, r.usage.model_dump() if getattr(r, "usage", None) else None, req["model"])

    def sse():
        for e in events:
            yield f"data: {json.dumps(e)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})
