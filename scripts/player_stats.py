"""NBA player game logs from ESPN's public API, for the players that have props.

Output (goes into the encrypted bundle as bundle["stats"]["nba"]):
  {"updated": TS,
   "teams": {"Los Angeles Lakers": "LAL", ...},
   "players": {norm(bookie name): {"n": ESPN name, "id": ESPN id, "team": "LAL", "f": TS fetched,
                                   "g": [[date, opp, "@"|"vs", min, pts, reb, ast, 3pm, blk, stl, tov], ...]}}}
Games are newest first, this season's regular season + playoffs only (no preseason).
Everything here is best effort: any failure keeps the previous stats and never stops the odds run.
"""
import json, re, time, unicodedata, urllib.request
from datetime import datetime, timedelta

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
WEB = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"
REFRESH = timedelta(hours=6)     # re-pull a player's log at most this often
KEEP = timedelta(days=4)         # drop players who haven't had props for this long
MAX_CALLS = 320
COLS = ["minutes", "totalRebounds", "assists", "blocks", "steals", "turnovers", "points"]


def norm(name):
    s = unicodedata.normalize("NFD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'’`-]", " ", s)
    toks = [t for t in s.split() if t not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(toks)


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (overlay-tips-odds)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def parse_gamelog(gl):
    names = gl.get("names") or []
    idx = {n: i for i, n in enumerate(names)}
    tp = next((i for i, n in enumerate(names) if n.startswith("threePointFieldGoalsMade")), None)
    meta = gl.get("events") or {}
    rows = []
    for st in gl.get("seasonTypes") or []:
        if "preseason" in (st.get("displayName") or "").lower():
            continue
        for cat in st.get("categories") or []:
            for e in cat.get("events") or []:
                m, s = meta.get(str(e.get("eventId"))), e.get("stats") or []
                if not m or len(s) < len(names):
                    continue
                g = {c: _num(s[idx[c]]) if c in idx else 0.0 for c in COLS}
                tpm = _num(str(s[tp]).split("-")[0]) if tp is not None else 0.0
                when = datetime.fromisoformat(m["gameDate"].replace("Z", "+00:00")) - timedelta(hours=6)  # US date
                rows.append([when.date().isoformat(), (m.get("opponent") or {}).get("abbreviation", ""),
                             m.get("atVs", ""), round(g["minutes"]), int(g["points"]), int(g["totalRebounds"]),
                             int(g["assists"]), int(tpm), int(g["blocks"]), int(g["steals"]), int(g["turnovers"])])
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows


def _match(desc, roster):
    """roster: {norm name: athlete}. Exact name first, then first initial + surname."""
    n = norm(desc)
    if n in roster:
        return roster[n]
    parts = n.split()
    if len(parts) >= 2:
        hits = [a for k, a in roster.items() if k.split()[-1] == parts[-1] and k[:1] == parts[0][:1]]
        if len(hits) == 1:
            return hits[0]
        hits = [a for k, a in roster.items() if k.split()[-1] == parts[-1]]
        if len(hits) == 1:
            return hits[0]
    return None


def update(prev, prop_events, now, ts):
    """prev: previous bundle["stats"]["nba"] (or None). prop_events: NBA prop events (Odds API format)."""
    out = {"updated": ts, "teams": dict((prev or {}).get("teams") or {}), "players": dict((prev or {}).get("players") or {})}
    nba = [e for e in prop_events if e.get("sport_key") == "basketball_nba"]
    if not nba:
        return out, "no NBA props"
    calls, fails = 0, 0
    teams = _get(f"{SITE}/teams")
    calls += 1
    tid = {}
    for t in teams["sports"][0]["leagues"][0]["teams"]:
        t = t["team"]
        out["teams"][t["displayName"]] = t["abbreviation"]
        tid[t["displayName"]] = t["id"]
    rosters = {}
    for ev in nba:
        for side in (ev["home_team"], ev["away_team"]):
            if side in rosters or side not in tid:
                continue
            try:
                ro = _get(f"{SITE}/teams/{tid[side]}/roster")
                calls += 1
                rosters[side] = {norm(a.get("displayName") or a.get("fullName")): {**a, "_team": out["teams"][side]}
                                 for a in ro.get("athletes", [])}
            except Exception:
                fails += 1
    seen, unmatched = set(), []
    for ev in nba:
        roster = {**rosters.get(ev["home_team"], {}), **rosters.get(ev["away_team"], {})}
        descs = {o.get("description") for b in ev.get("bookmakers", []) for m in b.get("markets", [])
                 for o in m.get("outcomes", []) if o.get("description")}
        for d in sorted(descs):
            key = norm(d)
            if key in seen:
                continue
            seen.add(key)
            old = out["players"].get(key)
            if old and now - datetime.fromisoformat(old["f"].replace("Z", "+00:00")) < REFRESH:
                old["s"] = ts
                continue
            a = _match(d, roster)
            if not a:
                unmatched.append(d)
                continue
            if calls >= MAX_CALLS:
                break
            try:
                gl = _get(f"{WEB}/athletes/{a['id']}/gamelog")
                calls += 1
                out["players"][key] = {"n": a.get("displayName"), "id": a["id"], "team": a["_team"],
                                       "f": ts, "s": ts, "g": parse_gamelog(gl)}
            except Exception:
                fails += 1
                if old:
                    old["s"] = ts
            time.sleep(0.05)
    # forget players who haven't had props for a while
    cut = now - KEEP
    out["players"] = {k: v for k, v in out["players"].items()
                      if datetime.fromisoformat(v.get("s", v["f"]).replace("Z", "+00:00")) > cut}
    note = f"{len(seen)} NBA prop players, {calls} ESPN calls, {fails} failed"
    if unmatched:
        note += f", unmatched: {', '.join(unmatched[:12])}"
    return out, note
