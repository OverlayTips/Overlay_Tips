"""One-off check: API-Basketball (API-Sports) NBL player box scores. Prints a summary only."""
import json, os, urllib.error, urllib.request

KEY = os.environ.get("APISPORTS_KEY", "").strip()
BASE = "https://v1.basketball.api-sports.io"


def get(path):
    req = urllib.request.Request(BASE + path, headers={"x-apisports-key": KEY, "User-Agent": "overlay-tips/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{path}: HTTP {e.code} {e.read()[:300]!r}")


st = get("/status")
print("plan:", json.dumps(st.get("response", {}).get("subscription", {})), json.dumps(st.get("response", {}).get("requests", {})))
g = get("/games?league=1&season=2026-2027")
games = g.get("response", [])
print(f"games: {len(games)} errors: {g.get('errors')}")
fin = [x for x in games if x["status"]["short"] in ("FT", "AOT")]
ns = [x for x in games if x["status"]["short"] == "NS"]
live = [x for x in games if x["status"]["short"] not in ("FT", "AOT", "NS", "CANC", "POST")]
print(f"finished {len(fin)}, upcoming {len(ns)}, live now {len(live)}")
for x in live:
    print("LIVE:", x["status"], x["teams"]["home"]["name"], x["scores"]["home"]["total"], "v", x["teams"]["away"]["name"], x["scores"]["away"]["total"])
for x in ns[:4]:
    print("next:", x["date"], x["teams"]["home"]["name"], "v", x["teams"]["away"]["name"], x["id"])
for last in fin[-2:]:
    print("\nGAME", last["id"], last["date"], last["teams"]["home"]["name"], last["scores"]["home"]["total"],
          "v", last["teams"]["away"]["name"], last["scores"]["away"]["total"])
    ps = get(f"/games/statistics/players?ids={last['id']}")
    rows = ps.get("response", [])
    print("player rows:", len(rows), "errors:", ps.get("errors"))
    if rows:
        print("keys:", json.dumps(rows[0])[:700])
    for r in sorted(rows, key=lambda r: -(r.get("points") or 0))[:8]:
        rb = r.get("rebounds") or {}
        print(f"  {r['player']['name']:<24} {r.get('team',{}).get('id')} min {r.get('minutes')} pts {r.get('points')} reb {rb.get('total') if isinstance(rb, dict) else rb} ast {r.get('assists')} 3pm {(r.get('threepoint_goals') or {}).get('total')}")
# a player's season log
if fin:
    ps = get(f"/games/statistics/players?ids={fin[-1]['id']}")
    pid = (ps.get("response") or [{}])[0].get("player", {}).get("id")
    if pid:
        lg = get(f"/games/statistics/players?player={pid}&season=2026-2027")
        print("\nseason log for player", pid, ":", len(lg.get("response", [])), "games", lg.get("errors"))
for x in live[:1]:
    ps = get(f"/games/statistics/players?ids={x['id']}")
    print("\nLIVE box rows:", len(ps.get("response", [])), json.dumps(ps.get("response", [])[:1])[:400])
