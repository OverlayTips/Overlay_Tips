"""NBL player game logs from API-Basketball (API-Sports, paid Pro plan: APISPORTS_KEY secret).

Same output shape as player_stats.py so hot.py can use it unchanged:
  {"updated": TS, "src": "apisports", "season": "2026-2027", "done": [game ids already pulled],
   "teams": {Odds API team name: API team name},
   "by": {bookie player name: player key},
   "players": {key: {"n": name, "id": API id, "team": API team name,
                     "g": [[date, opp, "@"|"vs", min, pts, reb, ast, 3pm, 0, 0, 0, team], ...]}}}   newest first
API-Basketball has no steals/blocks/turnovers, so those stay 0 (hot.py skips those markets for NBL).
Best effort: any failure keeps the previous data and never stops the odds run.
"""
import json, os, re, time, unicodedata, urllib.error, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = "https://v1.basketball.api-sports.io"
KEY = os.environ.get("APISPORTS_KEY", "").strip()
LEAGUE = 1            # API-Basketball "NBL" (Australia)
BATCH = 10            # game ids per box-score request
MAX_CALLS = 60
SYD = ZoneInfo("Australia/Sydney")


def norm(name):
    s = unicodedata.normalize("NFD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'’`]", " ", s).replace("-", " ")
    return " ".join(t for t in s.split() if t not in ("jr", "sr", "ii", "iii", "iv"))


def _season(now):
    y = now.year if now.month >= 7 else now.year - 1
    return f"{y}-{y + 1}"


def _mins(m):
    try:
        mm, _, ss = str(m or "0").partition(":")
        return int(mm) + (1 if ss and int(ss) >= 30 else 0)
    except ValueError:
        return 0


class _Api:
    def __init__(self):
        self.calls = 0

    def get(self, path):
        req = urllib.request.Request(BASE + path, headers={"x-apisports-key": KEY, "User-Agent": "overlay-tips/1.0"})
        for attempt in range(3):
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    j = json.load(r)
                if j.get("errors") and "rateLimit" in json.dumps(j["errors"]) and attempt < 2:
                    time.sleep(6)
                    continue
                return j
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(6)
                    continue
                raise


def _team_map(odds_names, api_names):
    """Odds API 'Sydney Kings' -> API-Basketball 'Sydney'; 'South East Melbourne Phoenix' -> 'South East Melbourne'."""
    out = {}
    for o in odds_names:
        no = norm(o)
        best = max(api_names, key=lambda a: (no.startswith(norm(a)) or norm(a) in no, len(norm(a))), default=None)
        if best and (no.startswith(norm(best)) or norm(best) in no):
            out[o] = best
    return out


def _match(desc, cands):
    """Bookie name 'Bryce Cotton' vs API names 'Cotton Bryce' / 'B. Cotton' / 'Bryce Cotton'."""
    toks = norm(desc).split()
    if not toks:
        return None
    full = set(toks)
    hits = [c for c in cands if set(norm(c["n"]).split()) == full]
    if len(hits) == 1:
        return hits[0]
    first, last = toks[0], toks[-1]
    hits = []
    for c in cands:
        ct = norm(c["n"]).split()
        if last in ct and any(t != last and (t == first or (len(t) == 1 and first.startswith(t))) for t in ct):
            hits.append(c)
    return hits[0] if len(hits) == 1 else None


def update(prev, prop_events, now, ts):
    nbl = [e for e in prop_events if e.get("sport_key") == "basketball_nbl"]
    season = _season(now)
    prev = prev if (prev or {}).get("src") == "apisports" and prev.get("season") == season else None
    out = {"updated": ts, "src": "apisports", "season": season, "done": list((prev or {}).get("done") or []),
           "teams": dict((prev or {}).get("teams") or {}), "raw": dict((prev or {}).get("raw") or {}),
           "players": {}, "by": {}}
    if not KEY:
        return out, "APISPORTS_KEY not set: no NBL stats"
    api = _Api()
    j = api.get(f"/games?league={LEAGUE}&season={season}")
    if j.get("errors"):
        return (prev or out), f"NBL games failed: {j['errors']}"
    games = {str(g["id"]): g for g in j.get("response", [])}
    fin = [gid for gid, g in games.items() if g["status"]["short"] in ("FT", "AOT") and gid not in out["done"]]
    for i in range(0, len(fin), BATCH):
        if api.calls >= MAX_CALLS:
            break
        chunk = fin[i:i + BATCH]
        r = api.get("/games/statistics/players?ids=" + "-".join(chunk))
        if r.get("errors"):
            print("  NBL box scores failed:", r["errors"])
            break
        for row in r.get("response", []):
            gid = str((row.get("game") or {}).get("id"))
            g = games.get(gid)
            if not g:
                continue
            tid = (row.get("team") or {}).get("id")
            home = g["teams"]["home"]["id"] == tid
            team = g["teams"]["home" if home else "away"]["name"]
            opp = g["teams"]["away" if home else "home"]["name"]
            mins = _mins(row.get("minutes"))
            if mins <= 0:
                continue
            date = datetime.fromisoformat(g["date"]).astimezone(SYD).strftime("%Y-%m-%d")
            reb = row.get("rebounds") or {}
            p = out["raw"].setdefault(str(row["player"]["id"]), {"n": row["player"]["name"], "team": team, "g": {}})
            p["n"], p["team"] = row["player"]["name"], team
            p["g"][gid] = [date, opp, "vs" if home else "@", mins, int(row.get("points") or 0),
                           int((reb.get("total") if isinstance(reb, dict) else reb) or 0), int(row.get("assists") or 0),
                           int((row.get("threepoint_goals") or {}).get("total") or 0), 0, 0, 0, team]
        out["done"] += chunk
        time.sleep(0.3)

    # players keyed for hot.py; latest team = team in their newest game
    cands = []
    for pid, p in out["raw"].items():
        rows = sorted(p["g"].values(), key=lambda x: x[0], reverse=True)
        p["team"] = rows[0][11] if rows else p["team"]
        cands.append({"id": pid, "n": p["n"], "team": p["team"], "rows": rows})
    api_teams = sorted({t["name"] for g in games.values() for t in (g["teams"]["home"], g["teams"]["away"])})
    odds_teams = {e[k] for e in nbl for k in ("home_team", "away_team")}
    out["teams"].update(_team_map(odds_teams, api_teams))

    unmatched = []
    for ev in nbl:
        pool = [c for c in cands if c["team"] in {out["teams"].get(ev["home_team"]), out["teams"].get(ev["away_team"])}] or cands
        descs = {o.get("description") for b in ev.get("bookmakers", []) for m in b.get("markets", [])
                 for o in m.get("outcomes", []) if o.get("description")}
        for d in sorted(descs):
            c = _match(d, pool)
            if not c:
                unmatched.append(d)
                continue
            key = f"nbl:{c['id']}"
            out["by"][d] = key
            out["players"][key] = {"n": d, "id": c["id"], "team": c["team"], "g": c["rows"]}
    note = (f"{len(games)} NBL games, {len(fin)} new box scores, {len(out['players'])} prop players matched, "
            f"{api.calls} API-Sports calls")
    if unmatched:
        note += f", unmatched: {', '.join(unmatched[:10])}"
    return out, note


def _given_first(n):
    """The feed mostly writes 'Cotton Bryce' (surname first): flip plain two-word names to 'Bryce Cotton'."""
    t = n.split()
    return f"{t[1]} {t[0]}" if len(t) == 2 and "." not in n else n


def player_list(st, ts):
    """Public list for the bet tracker's name search: bookie spelling where we have it, else the feed's."""
    named = {v["id"]: v["n"] for v in (st.get("players") or {}).values()}
    out = {}
    for pid, p in (st.get("raw") or {}).items():
        rows = sorted(p.get("g", {}).values(), key=lambda r: r[0], reverse=True)
        team = rows[0][11] if rows and len(rows[0]) > 11 else p.get("team", "")
        n = named.get(pid) or _given_first(p.get("n") or "")
        if n:
            out[n] = team
    return {"updated": ts, "players": [{"n": n, "t": t} for n, t in sorted(out.items())]}
