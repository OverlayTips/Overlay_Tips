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
for s in nbl["seasons"][-3:]:
    print("season", s["season"], "coverage", json.dumps(s.get("coverage")))
for season in [s["season"] for s in nbl["seasons"]][::-1][:3]:
    g = get(f"/games?league={nbl['id']}&season={season}")
    print(f"games {season}: {len(g.get('response', []))} errors: {g.get('errors')}")
    fin = [x for x in g.get("response", []) if x["status"]["short"] in ("FT", "AOT")]
    if not fin:
        continue
    last = fin[-1]
    print("last finished:", last["date"], last["teams"]["home"]["name"], last["scores"]["home"]["total"],
          "v", last["teams"]["away"]["name"], last["scores"]["away"]["total"])
    ps = get(f"/games/statistics/players?ids={last['id']}")
    print("player stats errors:", ps.get("errors"), "rows:", len(ps.get("response", [])))
    for r in ps.get("response", [])[:6]:
        print("  ", json.dumps(r)[:300])
    up = [x for x in g.get("response", []) if x["status"]["short"] == "NS"][:3]
    for x in up:
        print("upcoming:", x["date"], x["teams"]["home"]["name"], "v", x["teams"]["away"]["name"])
    break
