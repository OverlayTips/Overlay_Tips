"""Gemini (Google AI Studio) helper shared by the bot's AI extras.

    ask(prompt)                      -> text or None
    ask(prompt, json_out=True)       -> dict or None (reply parsed as JSON)
    ask(prompt, search=True)         -> grounded in a live Google search; sources() then lists the pages used
    ask(prompt, image=(bytes, mime)) -> reads an image (bet slips etc.)

Never raises: no key, the free limit hit or a bad reply all just return None, and callers carry on without it.

Env: GEMINI_API_KEY (no key = everything here is off), GEMINI_MODEL (default gemini-flash-latest),
GEMINI_MAX_PER_RUN (default 25) so one run can't burn the free tier's daily requests.
use_budget(state, per_day) adds a daily cap kept in the caller's saved state (value_alerts.py's alerted.json).
"""
import base64, json, os, urllib.error, urllib.request
from datetime import datetime, timezone

MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
MAX_PER_RUN = int(os.environ.get("GEMINI_MAX_PER_RUN", "25"))

_calls = 0
_day = None       # {"d": "YYYY-MM-DD", "n": calls today} inside the caller's state, see use_budget
_per_day = None
_last_sources = []


def enabled():
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def use_budget(state, per_day=200):
    """Count calls against a daily cap stored in state["gemini"] (UTC day, same as Google's reset roughly)."""
    global _day, _per_day
    d = datetime.now(timezone.utc).date().isoformat()
    g = state.get("gemini")
    if not isinstance(g, dict) or g.get("d") != d:
        g = state["gemini"] = {"d": d, "n": 0}
    g["at"] = datetime.now(timezone.utc).isoformat()   # value_alerts.py drops state entries without a recent "at"
    _day, _per_day = g, per_day


def left():
    """Calls still allowed this run (and today, if a daily budget is set)."""
    n = MAX_PER_RUN - _calls
    if _day is not None:
        n = min(n, _per_day - _day["n"])
    return max(0, n)


def sources():
    """[(title, url), ...] the last search-grounded call drew on."""
    return list(_last_sources)


def _json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    i, j = text.find("{"), text.rfind("}")
    return json.loads(text[i:j + 1]) if i >= 0 and j > i else None


def ask(prompt, json_out=False, search=False, image=None, max_tokens=800, timeout=45, label="gemini"):
    global _calls, _last_sources
    _last_sources = []
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or left() <= 0:
        return None
    parts = [{"text": prompt}]
    if image:
        data, mime = image
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
    cfg = {"maxOutputTokens": max_tokens}
    if json_out and not search:   # JSON mode and the search tool don't mix on every model: ask for JSON in words then
        cfg["responseMimeType"] = "application/json"
    body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": cfg}
    if search:
        body["tools"] = [{"google_search": {}}]
    req = urllib.request.Request(URL.format(MODEL), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    _calls += 1
    if _day is not None:
        _day["n"] += 1
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.load(r)
        cand = (res.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [])
        meta = cand.get("groundingMetadata") or {}
        _last_sources = [((c.get("web") or {}).get("title") or "", (c.get("web") or {}).get("uri") or "")
                         for c in meta.get("groundingChunks") or [] if (c.get("web") or {}).get("uri")]
        if not text.strip():
            return None
        return _json(text) if json_out else text.strip()
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")[:200]
        print(f"::warning::{label} (Gemini): HTTP {e.code} {msg}")
    except Exception as e:
        print(f"::warning::{label} (Gemini): {type(e).__name__} {e}")
    return None
