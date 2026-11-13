"""NBL live scores for the members' Live page + bet tracker.

API-Sports blocks requests from Cloudflare's shared Workers network, so GitHub fetches the NBL
scoreboard instead and publishes it ENCRYPTED to the nbl-live branch (nbl.json). The overlay-live
Worker decrypts it for logged-in members (key derived from LIVE_PASSWORD, which both sides have).

  python scripts/nbl_live.py once   -> one snapshot (used by the hourly NBL check)
  python scripts/nbl_live.py loop   -> every 30s while an NBL game is on or about to start (max ~5h)
Env: APISPORTS_KEY, LIVE_PASSWORD, OUT (default nbl-live), PUBLISH (shell command run after each write)
Same shape as the Worker's /nba answer: {"updated", "games": [{"id","lg","start","state","status",
"period","clock","home": {"abbr","name","score","players": [...]}, "away": {...}}]}
"""
import base64, hashlib, json, os, secrets, subprocess, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nbl_names

KEY = os.environ.get("APISPORTS_KEY", "").strip()
PW = os.environ.get("LIVE_PASSWORD", "").strip()
OUT = os.environ.get("OUT", "nbl-live")
SYD = ZoneInfo("Australia/Sydney")
ABBR = {"Adelaide": "ADL", "Brisbane": "BRI", "Cairns": "CNS", "Illawarra": "ILL", "Melbourne": "MEL",
        "New Zealand": "NZB", "Perth": "PER", "South East Melbourne": "SEM", "Sydney": "SYD", "Tasmania": "TAS"}
PER = {"Q1": 1, "Q2": 2, "HT": 2, "Q3": 3, "Q4": 4, "BT": 0, "OT": 5}
DONE = {"FT", "AOT", "POST", "CANC"}


def api(path):
    req = urllib.request.Request("https://v1.basketball.api-sports.io" + path,
                                 headers={"x-apisports-key": KEY, "User-Agent": "overlay-tips/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        j = json.load(r)
    if j.get("errors"):
        raise RuntimeError(json.dumps(j["errors"])[:200])
    return j.get("response", [])


def abbr(n):
    k = sorted((k for k in ABBR if (n or "").startswith(k)), key=len, reverse=True)
    return ABBR[k[0]] if k else (n or "")[:3].upper()


def snapshot():
    now = datetime.now(timezone.utc)
    y = now.year if now.month >= 7 else now.year - 1
    days = [(now.astimezone(SYD) + timedelta(days=d)).strftime("%Y-%m-%d") for d in (-1, 0, 1)]
    games = [g for g in api(f"/games?league=1&season={y}-{y + 1}&timezone=Australia/Sydney") if g["date"][:10] in days]
    started = [g["id"] for g in games if g["status"]["short"] not in ("NS", "CANC", "POST")
               and now - datetime.fromisoformat(g["date"]) < timedelta(hours=9)]
    rows = api("/games/statistics/players?ids=" + "-".join(map(str, started))) if started else []
    by = {}
    for r in rows:
        by.setdefault((r["game"]["id"], r["team"]["id"]), []).append(r)

    def side(g, t, score):
        ps = []
        for p in by.get((g["id"], t["id"]), []):
            tp, ft, rb = p.get("threepoint_goals") or {}, p.get("freethrows_goals") or {}, p.get("rebounds") or {}
            ps.append({"name": nbl_names.fix(p["player"]["name"]), "min": p.get("minutes") or "", "pts": p.get("points") or 0,
                       "reb": rb.get("total") or 0, "ast": p.get("assists") or 0, "fg3m": tp.get("total") or 0,
                       "fg3a": tp.get("attempts") or 0, "ftm": ft.get("total") or 0, "fta": ft.get("attempts") or 0,
                       "stl": 0, "blk": 0, "tov": 0, "pf": 0, "fgm": 0, "fga": 0, "oreb": 0, "dreb": 0, "pm": None,
                       "starter": p.get("type") == "starters"})
        return {"abbr": abbr(t["name"]), "name": t["name"], "score": score, "players": ps}

    out = []
    for g in games:
        st = g["status"]["short"]
        out.append({"id": f"nbl{g['id']}", "lg": "NBL", "start": g["date"],
                    "state": "pre" if st == "NS" else "post" if st in DONE else "in",
                    "status": g["status"].get("long") or st, "period": PER.get(st, 0),
                    "clock": "Half" if st == "HT" else (f"{g['status']['timer']}'" if g["status"].get("timer") else ""),
                    "home": side(g, g["teams"]["home"], (g.get("scores") or {}).get("home", {}).get("total")),
                    "away": side(g, g["teams"]["away"], (g.get("scores") or {}).get("away", {}).get("total"))})
    out.sort(key=lambda g: g["start"])
    return {"updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "games": out}


def encrypt(obj):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = hashlib.sha256(("nbl-live:" + PW).encode()).digest()
    iv = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(iv, json.dumps(obj, separators=(",", ":")).encode(), None)
    return {"v": 1, "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}


def write(snap):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "nbl.json"), "w") as f:
        json.dump(encrypt(snap), f)
    if os.environ.get("PUBLISH"):
        subprocess.run(os.environ["PUBLISH"], shell=True, check=False)


def busy(snap):
    """A game on now, tipping within 20 min, or 'postponed' in the last 3h (often just a delayed start)."""
    now = datetime.now(timezone.utc)
    # API-Sports sometimes marks a delayed tip-off "Postponed" and then flips it to live: keep watching those for 3h
    return any(g["state"] == "in" or (g["state"] == "pre" and datetime.fromisoformat(g["start"]) - now < timedelta(minutes=20))
               or ("postpon" in str(g["status"]).lower() and timedelta(0) <= now - datetime.fromisoformat(g["start"]) < timedelta(hours=3))
               for g in snap["games"])


if __name__ == "__main__":
    if not KEY or not PW:
        sys.exit("APISPORTS_KEY and LIVE_PASSWORD secrets are both needed")
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    end = time.time() + 5 * 3600
    while True:
        try:
            snap = snapshot()
            write(snap)
            live = [f"{g['away']['abbr']} {g['away']['score']}-{g['home']['score']} {g['home']['abbr']} ({g['status']})"
                    for g in snap["games"] if g["state"] == "in"]
            print(snap["updated"], "live:", ", ".join(live) or "none")
        except Exception as e:
            print("::warning::NBL snapshot failed:", e)
            snap = {"games": []}
        if mode != "loop" or time.time() > end or not busy(snap) and not any(g["state"] == "pre" for g in snap["games"]
                                                                            if datetime.fromisoformat(g["start"]) - datetime.now(timezone.utc) < timedelta(minutes=80)):
            break
        time.sleep(30)
