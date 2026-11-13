"""NBL pre-tip refresh: if an NBL game tips in the next 15-80 minutes, start an NBL-only odds update
(fresh NBL odds + props -> value picks and phone alerts before the game). Run hourly in the evening.
1 API-Sports call per run. Needs APISPORTS_KEY, GH_TOKEN (actions: write), GITHUB_REPOSITORY."""
import json, os, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone

KEY = os.environ.get("APISPORTS_KEY", "").strip()
now = datetime.now(timezone.utc)
y = now.year if now.month >= 7 else now.year - 1
req = urllib.request.Request(f"https://v1.basketball.api-sports.io/games?league=1&season={y}-{y + 1}",
                             headers={"x-apisports-key": KEY, "User-Agent": "overlay-tips/1.0"})
with urllib.request.urlopen(req, timeout=30) as r:
    games = json.load(r).get("response", [])
soon = [g for g in games if g["status"]["short"] == "NS"
        and timedelta(minutes=15) <= datetime.fromisoformat(g["date"]) - now <= timedelta(minutes=80)]
for g in soon:
    print("tips soon:", g["date"], g["teams"]["home"]["name"], "v", g["teams"]["away"]["name"])
if not soon:
    print("no NBL game in the next 15-80 min"); raise SystemExit(0)
tok, repo = os.environ["GH_TOKEN"], os.environ["GITHUB_REPOSITORY"]
req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/workflows/odds.yml/dispatches",
                             data=json.dumps({"ref": "main", "inputs": {"only": "basketball_nbl"}}).encode(),
                             headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                             method="POST")
with urllib.request.urlopen(req, timeout=20) as r:
    r.read()
print("NBL odds refresh started")
