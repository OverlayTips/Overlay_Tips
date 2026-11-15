"""Overlay Tips app alerts: web push straight to members' phones (no extra app), by tier and their own choices.

send(cat, title, lines, url, min_tier) pushes one alert to every member device that:
  - belongs to a member at min_tier or above ("founder" < "core" < "vip"; admins count as VIP), and
  - has that category switched on in the portal (nba, nfl, nbl, racing, news, games, arbs).
Devices that no longer exist (404/410) are removed. Best effort: never stops the caller.

Env: VAPID_PRIVATE_KEY (secret), SUPABASE_SERVICE_KEY (secret), SUPABASE_URL (optional)
"""
import json, os, urllib.parse, urllib.request
from datetime import datetime, timezone

SB = os.environ.get("SUPABASE_URL", "https://enldqvdxygppxwrauqlw.supabase.co").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
VAPID = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
CLAIMS = {"sub": "mailto:jayden@overlay.tips"}
RANK = {"pending": 0, "founder": 1, "core": 2, "vip": 3}
_SUBS = None


def enabled():
    return bool(SB_KEY and VAPID)


def _sb(method, path, body=None):
    req = urllib.request.Request(f"{SB}/rest/v1/{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json",
                                          "Prefer": "return=minimal"})
    with urllib.request.urlopen(req, timeout=20) as r:
        t = r.read()
        return json.loads(t) if t else None


def _subs():
    global _SUBS
    if _SUBS is None:
        _SUBS = _sb("GET", "push_subs?select=endpoint,p256dh,auth,prefs,profiles(tier,is_admin)") or []
    return _SUBS


def send(cat, title, lines, url="https://overlay.tips/", min_tier="core"):
    """Returns how many devices it reached."""
    if not enabled():
        return 0
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("::warning::web push: pywebpush not installed")
        return 0
    body = "\n".join(l for l in (lines or []) if l)
    payload = json.dumps({"title": title[:120], "body": body[:600], "url": url, "tag": f"{cat}-{abs(hash(title)) % 10**8}"})
    ok, gone = 0, []
    try:
        subs = _subs()
    except Exception as e:
        print(f"::warning::web push: couldn't read devices: {e}")
        return 0
    for s in subs:
        p = s.get("profiles") or {}
        tier = "vip" if p.get("is_admin") else p.get("tier") or "pending"
        if RANK.get(tier, 0) < RANK[min_tier] or (s.get("prefs") or {}).get(cat) is False:
            continue
        try:
            webpush({"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}}, payload,
                    vapid_private_key=VAPID, vapid_claims=dict(CLAIMS), ttl=3600)
            ok += 1
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):
                gone.append(s["endpoint"])
            else:
                print(f"::warning::web push failed ({code})")
        except Exception as e:
            print(f"::warning::web push failed: {type(e).__name__}")
    for ep in gone:
        try:
            _sb("DELETE", "push_subs?endpoint=eq." + urllib.parse.quote(ep, safe=""))
        except Exception:
            pass
    if ok or gone:
        print(f"web push [{cat}]: {ok} devices" + (f", removed {len(gone)} dead" if gone else ""))
    return ok
