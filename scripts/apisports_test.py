"""One-off check: does API-Basketball (API-Sports) have NBL player box scores? Prints a summary only."""
import json, os, urllib.error, urllib.request

KEY = os.environ.get("APISPORTS_KEY", "").strip()
BASE = "https://v1.basketball.api-sports.io"


def get(path):
    req = urllib.request.Request(BASE + path, headers={"x-apisports-key": KEY, "User-Agent": "overlay-tips/1.0",
                                                       "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{path}: HTTP {e.code} {e.read()[:300]!r} (key length {len(KEY)})")


if not KEY:
    raise SystemExit("APISPORTS_KEY secret is empty")
st = get("/status")
print("status:", json.dumps(st.get("response", {}).get("subscription", {})), "errors:", st.get("errors"))
lg = get("/leagues?country=Australia")
print("errors:", lg.get("errors"))
nbl = None
for l in lg.get("response", []):
    print("league", l["id"], l["name"], l["type"], [s["season"] for s in l.get("seasons", [])][-4:])
    if l["name"].strip().upper() == "NBL" and not nbl:
        nbl = l
if not nbl:
    raise SystemExit("no NBL league found")
seasons = sorted(nbl["seasons"], key=lambda s: str(s["season"]))
for s in seasons:
    c = s.get("coverage") or {}
    print("season", s["season"], "player stats:", c.get("games", {}).get("statistics", {}).get("players"),
          "players:", c.get("players"), "odds:", c.get("odds"))
cur = seasons[-1]["season"]
g = get(f"/games?league={nbl['id']}&season={cur}")
print(f"games {cur}: {len(g.get('response', []))} errors: {g.get('errors')}")
