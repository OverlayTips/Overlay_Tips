"""NFL player game logs from BALLDONTLIE (NFL GOAT plan, same NBA_STATS_KEY secret: one key per account).

Same output shape as player_stats.py / nbl_stats.py so hot.py can use it unchanged:
  {"updated": TS, "src": "balldontlie-nfl", "season": 2026, "done": [game ids already pulled],
   "teams": {Odds API team name: abbr},
   "by": {bookie player name: player key},
   "players": {key: {"n": name, "id": BDL id, "team": abbr, "pos": "QB",
                     "g": [[date, opp, "@"|"vs", 1, pyd, ptd, cmp, patt, int, ryd, ratt, rec, ryds, tgt], ...]}}}
newest first. Slot 3 is 1 for "played" (the NBA logs keep minutes there). Only players with passing, rushing
or receiving numbers are kept, plus every game a QB/RB/WR/TE played, even with zero numbers. Each run pulls box scores for finished games it hasn't seen yet.
Best effort: any failure keeps the previous data and never stops the odds run.
"""
import json, os, re, time, unicodedata, urllib.error, urllib.parse, urllib.request
from datetime import datetime

BASE = "https://api.balldontlie.io/nfl/v1"
KEY = os.environ.get("NBA_STATS_KEY", "").strip()
MAX_CALLS = 150
BATCH = 8   # game ids per stats request
SKILL = {"QB", "RB", "FB", "WR", "TE"}   # keep their 0-catch / 0-carry games: they count for Unders and hit rates
VERSION = 2   # bump to rebuild the season's logs from scratch (v2: keep skill players' zero games)


def norm(name):
    s = unicodedata.normalize("NFD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'’`]", "", s).replace("-", " ")
    return " ".join(t for t in s.split() if t not in ("jr", "sr", "ii", "iii", "iv", "v"))


def season_of(now):
    return now.year if now.month >= 8 else now.year - 1


class _Api:
    def __init__(self):
        self.calls = 0

    def get(self, path, params):
        qs = urllib.parse.urlencode(params, doseq=True)
        req = urllib.request.Request(f"{BASE}{path}?{qs}", headers={"Authorization": KEY, "User-Agent": "overlay-tips/1.0"})
        for attempt in range(3):
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(5)
                    continue
                raise

    def all(self, path, params):
        out, cursor = [], None
        while self.calls < MAX_CALLS:
            p = dict(params, per_page=100, **({"cursor": cursor} if cursor else {}))
            j = self.get(path, p)
            out += j.get("data") or []
            cursor = (j.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
        return out


def _row(s, g, tid):
    home = (g.get("home_team") or {}).get("id") == tid
    opp = (g.get("visitor_team") if home else g.get("home_team")) or {}
    n = lambda k: s.get(k) or 0
    return [str(g.get("date") or "")[:10], opp.get("abbreviation") or "", "vs" if home else "@", 1,
            n("passing_yards"), n("passing_touchdowns"), n("passing_completions"), n("passing_attempts"),
            n("passing_interceptions"), n("rushing_yards"), n("rushing_attempts"), n("receptions"),
            n("receiving_yards"), n("receiving_targets")]


def _match(desc, players, idx):
    k = norm(desc)
    if k in players:
        return k
    toks = k.split()   # "Ken Walker III" / "Kenneth Walker": first initial + surname, only if it's unique
    if len(toks) >= 2:
        c = idx.get((toks[0][0], toks[-1]))
        if c and c != "dup":
            return c
    return None


def update(prev, prop_events, now, ts):
    prev = prev or {}
    if not KEY:
        return prev, "NBA_STATS_KEY secret not set: no NFL player stats"
    season = season_of(now)
    fresh = prev.get("season") != season or prev.get("v") != VERSION
    data = {"season": season, "v": VERSION, "done": [], "players": {}} if fresh else prev
    data.setdefault("done", []); data.setdefault("players", {})
    api = _Api()
    note = ""
    try:
        games = api.all("/games", {"seasons[]": season, "season_types[]": [2, 3]})
        teams = {}
        for g in games:
            for t in (g.get("home_team"), g.get("visitor_team")):
                if t and t.get("full_name"):
                    teams[t["full_name"]] = t.get("abbreviation") or ""
        data["teams"] = teams or data.get("teams") or {}
        done = set(data["done"])
        todo = [g for g in games if g.get("status_state") == "final" and g["id"] not in done]
        todo.sort(key=lambda g: g.get("date") or "")
        byid = {g["id"]: g for g in games}
        pulled = 0
        for i in range(0, len(todo), BATCH):
            if api.calls >= MAX_CALLS - 3:
                break
            chunk = todo[i:i + BATCH]
            rows = api.all("/stats", {"game_ids[]": [g["id"] for g in chunk]})
            for s in rows:
                pl, gm, tm = s.get("player") or {}, byid.get((s.get("game") or {}).get("id")), s.get("team") or {}
                if not pl.get("id") or not gm:
                    continue
                r = _row(s, gm, tm.get("id"))
                if not any(r[4:]) and pl.get("position_abbreviation") not in SKILL:
                    continue   # no passing/rushing/receiving numbers (linemen, defence, kickers)
                name = f"{pl.get('first_name', '')} {pl.get('last_name', '')}".strip()
                k = norm(name)
                p = data["players"].setdefault(k, {"n": name, "id": pl["id"], "g": []})
                p.update({"team": tm.get("abbreviation") or p.get("team"), "pos": pl.get("position_abbreviation") or p.get("pos")})
                p["g"] = [x for x in p["g"] if x[0] != r[0]] + [r]
                p["g"].sort(key=lambda x: x[0], reverse=True)
            data["done"] += [g["id"] for g in chunk]
            pulled += len(chunk)
        note = f"{pulled} new games, {len(data['done'])} total, {len(data['players'])} players, {api.calls} calls"
    except Exception as e:
        note = f"kept previous ({type(e).__name__}: {e})"
    # bookie names -> player keys, for the NFL props on offer
    idx = {}
    for k in data["players"]:
        t = k.split()
        if len(t) >= 2:
            idx[(t[0][0], t[-1])] = "dup" if (t[0][0], t[-1]) in idx else k
    by = {}
    for ev in prop_events:
        if ev.get("sport_key") != "americanfootball_nfl":
            continue
        for b in ev.get("bookmakers", []):
            for m in b.get("markets", []):
                for o in m.get("outcomes", []):
                    d = o.get("description")
                    if d and d not in by:
                        k = _match(d, data["players"], idx)
                        if k:
                            by[d] = k
    data.update({"updated": ts, "src": "balldontlie-nfl", "by": by})
    return data, note


def player_list(data, ts):
    """Public list for the bet tracker's NFL player search: names + team only."""
    ps = sorted(((p["n"], p.get("team") or "") for p in (data.get("players") or {}).values()), key=lambda x: x[0])
    return {"updated": ts, "players": [{"n": n, "t": t} for n, t in ps]}
