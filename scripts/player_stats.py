"""NBA player game logs from BALLDONTLIE (licensed, paid API), for the players that have props.

Needs the NBA_STATS_KEY secret (a BALLDONTLIE key). Their terms allow commercial use; the key must
never be shared or put in a web page, so this only ever runs on GitHub's servers.
Docs: https://docs.balldontlie.io  (Authorization header = the key; cursor pagination)

Output (goes into the encrypted bundle as bundle["stats"]["nba"], same shape hot.py has always used):
  {"updated": TS, "src": "balldontlie",
   "teams": {"Los Angeles Lakers": "LAL", ...},               # Odds API team name -> abbreviation
   "by": {bookie name as written: player key},
   "players": {norm(name): {"n": name, "id": BDL id, "team": "LAL", "f": TS fetched, "s": TS last seen,
                            "g": [[date, opp, "@"|"vs", min, pts, reb, ast, 3pm, blk, stl, tov], ...]}}}
Games are newest first, this season only (regular season + playoffs), games he actually played.
Everything here is best effort: any failure keeps the previous stats and never stops the odds run.
"""
import json, os, re, time, unicodedata, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta

API = "https://api.balldontlie.io/v1"
KEY = os.environ.get("NBA_STATS_KEY", "").strip()
REFRESH = timedelta(hours=6)     # re-pull a player's log at most this often
KEEP = timedelta(days=4)         # drop players who haven't had props for this long
BATCH = 25                       # players per /stats request
MAX_CALLS = 200                  # GOAT allows 600/min; stay well under
ALIASES = {"LA Clippers": "Los Angeles Clippers", "Los Angeles Clippers": "LA Clippers"}


def norm(name):
    s = unicodedata.normalize("NFD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'’`]", "", s)
    s = re.sub(r"[-]", " ", s)
    toks = [t for t in s.split() if t not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(toks)


class _Api:
    def __init__(self):
        self.calls = 0

    def get(self, path, params=()):
        url = f"{API}{path}" + ("?" + urllib.parse.urlencode(list(params)) if params else "")
        req = urllib.request.Request(url, headers={"Authorization": KEY, "Accept": "application/json"})
        for attempt in range(3):
            try:
                self.calls += 1
                with urllib.request.urlopen(req, timeout=25) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:   # rate limited: back off and retry
                    time.sleep(5 * (attempt + 1))
                    continue
                raise

    def pages(self, path, params=(), limit=50):
        cursor, out = None, []
        for _ in range(limit):
            p = list(params) + [("per_page", 100)] + ([("cursor", cursor)] if cursor else [])
            j = self.get(path, p)
            out += j.get("data") or []
            cursor = (j.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
        return out


def _mins(m):
    m = str(m or "0").split(":")[0]
    try:
        return int(float(m))
    except ValueError:
        return 0


def _season(now):
    return now.year if now.month >= 9 else now.year - 1   # NBA season "2026" = 2026-27


def rows_from_stats(stats, abbr_by_id):
    """BDL /stats rows for ONE player -> our game rows, newest first, played + finished games only."""
    rows = {}
    for s in stats:
        g = s.get("game") or {}
        if not str(g.get("status", "")).startswith("Final") or _mins(s.get("min")) <= 0:
            continue
        tid = (s.get("team") or {}).get("id")
        home = g.get("home_team_id") == tid
        opp = abbr_by_id.get(g.get("visitor_team_id") if home else g.get("home_team_id"), "")
        n = lambda k: int(s.get(k) or 0)
        rows[g.get("id")] = [str(g.get("date", ""))[:10], opp, "vs" if home else "@", _mins(s.get("min")),
                             n("pts"), n("reb"), n("ast"), n("fg3m"), n("blk"), n("stl"), n("turnover")]
    return sorted(rows.values(), key=lambda r: r[0], reverse=True)


def _match(desc, roster):
    """roster: {norm name: player}. Exact name first, then first initial + surname."""
    n = norm(desc)
    if n in roster:
        return roster[n]
    parts = n.split()
    if len(parts) >= 2:
        hits = [a for k, a in roster.items() if k.split()[-1] == parts[-1] and k[:1] == parts[0][:1]]
        if len(hits) == 1:
            return hits[0]
    return None


def update(prev, prop_events, now, ts, extra=()):
    """prev: previous bundle["stats"]["nba"] (or None). prop_events: NBA prop events (Odds API format).
    extra: more player names to keep logs for (injured teammates, for the with/without numbers)."""
    prev = prev if (prev or {}).get("src") == "balldontlie" else None   # don't mix in old ESPN data
    out = {"updated": ts, "src": "balldontlie", "teams": dict((prev or {}).get("teams") or {}),
           "players": dict((prev or {}).get("players") or {})}
    nba = [e for e in prop_events if e.get("sport_key") == "basketball_nba"]
    if not KEY:
        return out, "NBA_STATS_KEY secret not set: no player stats (Hot stats stays empty)"
    if not nba:
        return out, "no NBA props"
    api, fails = _Api(), 0

    teams = api.get("/teams").get("data") or []
    abbr_by_id = {}
    for t in teams:
        if not t.get("conference"):          # BDL also lists historic franchises without a conference
            continue
        abbr_by_id[t["id"]] = t["abbreviation"]
        for nm in {t.get("full_name"), ALIASES.get(t.get("full_name"))} - {None}:
            out["teams"][nm] = t["abbreviation"]

    # every active player, so traded players still match; cached for a day
    ids = (prev or {}).get("ids") or {}
    if not ids.get("map") or now - datetime.fromisoformat(ids["f"].replace("Z", "+00:00")) > timedelta(hours=20):
        try:
            m = {}
            for p in api.pages("/players/active", limit=12):
                nm = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
                m[norm(nm)] = [p["id"], (p.get("team") or {}).get("abbreviation", ""), nm]
            if len(m) > 300:
                ids = {"f": ts, "map": m}
        except Exception as e:
            fails += 1
            print("  active players failed:", type(e).__name__, e)
    out["ids"] = ids
    roster = {k: {"id": v[0], "team": v[1], "n": v[2]} for k, v in (ids.get("map") or {}).items()}

    seen, unmatched, by, todo = set(), [], {}, {}
    for ev in nba:
        descs = {o.get("description") for b in ev.get("bookmakers", []) for mk in b.get("markets", [])
                 for o in mk.get("outcomes", []) if o.get("description")}
        for d in sorted(descs):
            key = norm(d)
            by[d] = key
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
                if old:
                    old["s"] = ts
                continue
            todo[a["id"]] = (key, a)

    for d in extra:   # injured players: their game dates tell us which games their teammates played without them
        key = norm(d)
        if key in seen:
            continue
        seen.add(key)
        old = out["players"].get(key)
        if old and now - datetime.fromisoformat(old["f"].replace("Z", "+00:00")) < REFRESH * 2:
            old["s"] = ts
            continue
        a = _match(d, roster)
        if a:
            todo[a["id"]] = (key, a)
        elif old:
            old["s"] = ts

    # game logs, BATCH players per request
    season, items = _season(now), list(todo.items())
    for i in range(0, len(items), BATCH):
        if api.calls >= MAX_CALLS:
            break
        chunk = items[i:i + BATCH]
        try:
            stats = api.pages("/stats", [("seasons[]", season)] + [("player_ids[]", pid) for pid, _ in chunk], limit=20)
        except Exception as e:
            fails += 1
            print("  stats batch failed:", type(e).__name__, e)
            for _, (key, _) in chunk:
                if key in out["players"]:
                    out["players"][key]["s"] = ts
            continue
        per = {}
        for s in stats:
            per.setdefault((s.get("player") or {}).get("id"), []).append(s)
        for pid, (key, a) in chunk:
            out["players"][key] = {"n": a["n"], "id": pid, "team": a["team"], "f": ts, "s": ts,
                                   "g": rows_from_stats(per.get(pid, []), abbr_by_id)}
        time.sleep(0.2)

    # forget players who haven't had props for a while
    cut = now - KEEP
    out["players"] = {k: v for k, v in out["players"].items()
                      if datetime.fromisoformat(v.get("s", v["f"]).replace("Z", "+00:00")) > cut}
    out["by"] = {d: k for d, k in by.items() if k in out["players"]}
    out["by"].update({d: k for d, k in ((prev or {}).get("by") or {}).items() if k in out["players"] and d not in out["by"]})
    note = f"{len(seen)} NBA prop players, {len(todo)} refreshed, {api.calls} BALLDONTLIE calls, {fails} failed"
    if unmatched:
        note += f", unmatched: {', '.join(unmatched[:12])}"
    return out, note
