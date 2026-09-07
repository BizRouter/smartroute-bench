"""Minimal LLM client for OpenAI-compatible chat/completions and Anthropic
messages wire formats. Standard library only.

Every call returns a normalized record:
{
  "ok": bool, "status": int, "error": str|None,
  "text": str,                  # assistant text
  "model_reported": str|None,   # response `model` field
  "routed_model": str|None,     # x-bizrouter-routed-model header (if present)
  "routed_provider": str|None,
  "usage": {...},               # raw usage block
  "in_tokens": int, "out_tokens": int,
  "cost_reported": float|None,  # usage.cost if present (provider currency)
  "latency_ms": int,
}
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

RETRYABLE = {429, 500, 502, 503, 504}


def _post(url: str, payload: dict, headers: dict, timeout: int = 600):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), json.loads(r.read().decode("utf-8"))


def call_llm(*, base_url: str, api_key: str, wire: str, model: str,
             messages: list[dict], system: str | None = None,
             max_tokens: int = 4096, session_id: str | None = None,
             attempt_id: str | None = None,
             retries: int = 3, timeout: int = 600) -> dict:
    """One non-streaming completion over the given wire ("chat" | "messages")."""
    headers = {"Authorization": f"Bearer {api_key}"}
    if session_id:
        headers["X-Session-Id"] = session_id
    if attempt_id:
        headers["X-BizRouter-Usage-Subject"] = attempt_id

    if wire == "messages":
        url = base_url.rstrip("/") + "/messages"
        headers["anthropic-version"] = "2023-06-01"
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if system:
            payload["system"] = system
    else:
        url = base_url.rstrip("/") + "/chat/completions"
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        payload = {"model": model, "messages": msgs, "max_tokens": max_tokens,
                   "stream": False}

    last_err, status = None, 0
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            status, hdrs, body = _post(url, payload, headers, timeout=timeout)
            lat = int((time.time() - t0) * 1000)
            return _normalize(wire, status, hdrs, body, lat)
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                last_err = e.read().decode("utf-8")[:500]
            except Exception:  # noqa: BLE001
                last_err = str(e)
            if status not in RETRYABLE or attempt == retries:
                break
        except Exception as e:  # noqa: BLE001 — timeouts, connection resets
            last_err = f"{type(e).__name__}: {e}"[:500]
            status = 0
            if attempt == retries:
                break
        time.sleep(min(2 ** attempt * 2, 20))

    return {"ok": False, "status": status, "error": last_err, "text": "",
            "model_reported": None, "routed_model": None, "routed_provider": None,
            "gateway_meta": None, "usage": None, "in_tokens": 0, "out_tokens": 0,
            "cost_reported": None, "latency_ms": 0, "finish": None}


def _normalize(wire: str, status: int, hdrs: dict, body: dict, latency_ms: int) -> dict:
    h = {k.lower(): v for k, v in hdrs.items()}
    usage = body.get("usage") or {}
    if wire == "messages":
        text = "".join(b.get("text", "") for b in body.get("content", [])
                       if isinstance(b, dict) and b.get("type") == "text")
        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        finish = body.get("stop_reason")
    else:
        ch = (body.get("choices") or [{}])[0]
        text = (ch.get("message") or {}).get("content") or ""
        in_tok = int(usage.get("prompt_tokens") or 0)
        out_tok = int(usage.get("completion_tokens") or 0)
        finish = ch.get("finish_reason")
    cost = usage.get("cost")
    return {
        "ok": True, "status": status, "error": None, "text": text,
        "model_reported": body.get("model"),
        "routed_model": h.get("x-bizrouter-routed-model"),
        "routed_provider": h.get("x-bizrouter-routed-provider"),
        # every x-bizrouter-* header, verbatim — picks up policy/candidate
        # metadata the gateway may expose later without a client change
        "gateway_meta": {k: v for k, v in h.items() if k.startswith("x-bizrouter-")},
        "usage": usage, "in_tokens": in_tok, "out_tokens": out_tok,
        "cost_reported": float(cost) if cost is not None else None,
        "latency_ms": latency_ms, "finish": finish,
    }
