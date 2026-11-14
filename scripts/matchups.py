"""NBA role + matchup context for player props (BALLDONTLIE GOAT, licensed; NBA_STATS_KEY secret).

update(prev, stats_nba, now, ts) -> (ctx, note)      kept in the encrypted bundle as stats["ctx"]
  teams  {abbr: {"pace", "drtg", "ortg"}}            team advanced season averages (every 6h)
  pos    what each defence gives up to guards / forwards / centres, built from every box score this season
         {"done": [game ids], "n": {abbr: games}, "agg": {abbr: {"G": [min, pts, reb, ast, 3pm, blk, stl], ...}}}
  usg    {BDL player id: usage %}                    season usage for the prop players (every 12h)

Then, per prop, hot.py asks:
  minutes(...)  projected minutes: recent minutes, projected/confirmed starter, injured teammates' freed
                minutes, blowout risk from the spread
  matchup(...)  how tonight's opponent defends his position for this stat (per 36 vs league average,
                and a rank), plus how fast tonight's game should be
Everything is best effort: any failure keeps the previous context and never stops the odds run.
"""
import json, os, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta

KEY = os.environ.get("NBA_STATS_KEY", "").strip()
V1, NV1 = "https://api.balldontlie.io/v1", "https://api.balldontlie.io/nba/v1"
TEAM_EVERY, POS_EVERY, USG_EVERY = timedelta(hours=6), timedelta(hours=3), timedelta(hours=12)
MAX_CALLS = 120
COLS = ("min", "pts", "reb", "ast", "fg3m", "blk", "stl")
STAT_COLS = {   # our hot.py market -> columns of COLS it sums
    "player_points": ("pts",), "player_rebounds": ("reb",), "player_assists": ("ast",), "player_threes": ("fg3m",),
    "player_blocks": ("blk",), "player_steals": ("stl",), "player_points_rebounds_assists": ("pts", "reb", "ast"),
    "player_points_rebounds": ("pts", "reb"), "player_points_assists": ("pts", "ast"), "player_rebounds_assists": ("reb", "ast"),
}
POS_NAME = {"G": "guards", "F": "forwards", "C": "centres"}


def _ts(x):
    return datetime.fromisoformat(str(x).replace("Z", "+00:00"))


class _Api:
    def __init__(self):
        self.calls = 0

    def get(self, url, params):
        req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                     headers={"Authorization": KEY, "Accept": "application/json"})
        for attempt in range(3):
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise

    def pages(self, url, params, limit=40):
        cur, out = None, []
        for _ in range(limit):
            if self.calls >= MAX_CALLS:
                break
            j = self.get(url, list(params) + [("per_page", 100)] + ([("cursor", cur)] if cur else []))
            out += j.get("data") or []
            cur = (j.get("meta") or {}).get("next_cursor")
            if not cur:
                break
        return out


def _mins(m):
    try:
        return float(str(m or "0").split(":")[0])
    except ValueError:
        return 0.0


def _num(d, *keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def bucket(pos):
    p = (pos or "").strip().upper()[:1]
    return p if p in POS_NAME else None


def update(prev, stats_nba, now, ts):
    ctx = dict(prev or {})
    if not KEY:
        return ctx, "no NBA_STATS_KEY"
    season = now.year if now.month >= 9 else now.year - 1
    if ctx.get("season") != season:
        ctx = {"season": season}
    api, notes = _Api(), []

    # ---- team pace / ratings ----
    if not ctx.get("t_f") or now - _ts(ctx["t_f"]) > TEAM_EVERY:
        try:
            rows = api.pages(f"{NV1}/team_season_averages/general",
                             [("season", season), ("season_type", "regular"), ("type", "advanced")], limit=2)
            teams = {}
            for r in rows:
                ab, st = (r.get("team") or {}).get("abbreviation"), r.get("stats") or {}
                pace = _num(st, "pace", "PACE")
                if ab and pace:
                    teams[ab] = {"pace": pace, "drtg": _num(st, "def_rating", "DEF_RATING"),
                                 "ortg": _num(st, "off_rating", "OFF_RATING")}
            if len(teams) >= 20:
                ctx["teams"], ctx["t_f"] = teams, ts
            notes.append(f"teams {len(teams)}")
        except Exception as e:
            notes.append(f"teams failed {type(e).__name__}")

    # ---- what each defence allows by position (incremental, from box scores) ----
    pos = ctx.setdefault("pos", {"done": [], "n": {}, "agg": {}, "last": None})
    if not ctx.get("p_f") or now - _ts(ctx["p_f"]) > POS_EVERY:
        try:
            ab_by_id = {}
            for t in api.get(f"{V1}/teams", []).get("data") or []:
                if t.get("conference"):
                    ab_by_id[t["id"]] = t["abbreviation"]
            # one date at a time so every game is complete; a backfill carries on over the next runs
            d = datetime.fromisoformat(pos["last"]).date() if pos.get("last") else datetime(season, 10, 15).date()
            today, done, added, caught_up = now.date(), set(pos["done"]), 0, True
            while d <= today:
                if api.calls >= MAX_CALLS - 6:
                    caught_up = False
                    break
                rows = api.pages(f"{V1}/stats", [("dates[]", d.isoformat())], limit=8)
                games = {}
                for r in rows:
                    g = r.get("game") or {}
                    if g.get("id") in done or not str(g.get("status", "")).startswith("Final"):
                        continue
                    games.setdefault(g["id"], (g, []))[1].append(r)
                for gid, (g, rs) in games.items():
                    ids = {g.get("home_team_id"), g.get("visitor_team_id")}
                    for tid in ids:
                        opp = ab_by_id.get(next(iter(ids - {tid}), None))
                        if opp:
                            pos["n"][opp] = pos["n"].get(opp, 0) + 1
                    for r in rs:
                        b = bucket((r.get("player") or {}).get("position"))
                        tid = (r.get("team") or {}).get("id")
                        opp = ab_by_id.get(next(iter(ids - {tid}), None))
                        m = _mins(r.get("min"))
                        if not b or not opp or m <= 0:
                            continue
                        agg = pos["agg"].setdefault(opp, {}).setdefault(b, [0.0] * len(COLS))
                        for i, v in enumerate([m] + [float(r.get(c) or 0) for c in COLS[1:]]):
                            agg[i] += v
                    done.add(gid)
                    added += 1
                pos["last"], pos["done"] = d.isoformat(), sorted(done)   # saved per date, so a failure never double counts
                d += timedelta(days=1)
            pos["done"] = sorted(done)
            if caught_up:
                ctx["p_f"] = ts
            notes.append(f"position defence: {len(done)} games (+{added}{'' if caught_up else ', backfilling'})")
        except Exception as e:
            notes.append(f"position defence failed {type(e).__name__}")

    # ---- usage for the prop players ----
    pids = sorted({p["id"] for p in (stats_nba.get("players") or {}).values() if p.get("id")})
    usg = ctx.setdefault("usg", {})
    if pids and (not ctx.get("u_f") or now - _ts(ctx["u_f"]) > USG_EVERY):
        try:
            got = 0
            for i in range(0, len(pids), 50):
                rows = api.pages(f"{V1}/season_averages/general",
                                 [("season", season), ("season_type", "regular"), ("type", "advanced")] +
                                 [("player_ids[]", x) for x in pids[i:i + 50]], limit=2)
                for r in rows:
                    u = _num(r.get("stats") or {}, "usg_pct", "usage_percentage", "e_usg_pct")
                    pid = (r.get("player") or {}).get("id")
                    if pid and u is not None:
                        usg[str(pid)] = round(u * 100 if u <= 1 else u, 1)
                        got += 1
            ctx["u_f"] = ts
            notes.append(f"usage {got}")
        except Exception as e:
            notes.append(f"usage failed {type(e).__name__}")
    return ctx, ", ".join(notes) + f", {api.calls} calls"


# ---------------- per-prop helpers (used by hot.py) ----------------
def _avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


def minutes(p, teammates, out_keys, starters, lineup_known, spread):
    """Projected minutes for player p (stats record with "g" rows: [date, opp, @/vs, min, ...]).
    teammates: other stats records on his team. out_keys: their keys if Out/Doubtful tonight.
    starters: True/False/None (projected or confirmed starter tonight; None = no lineup yet).
    spread: tonight's point spread for his team (negative = favourite) or None."""
    gs = [g for g in p.get("g", []) if g[3] > 0]
    if len(gs) < 3:
        return None
    l5, l10 = _avg([g[3] for g in gs[:5]]), _avg([g[3] for g in gs[:10]])
    base = 0.5 * l5 + 0.3 * l10 + 0.2 * _avg([g[3] for g in gs])
    why = []
    if starters is True and base < 26:
        base = (base + 30) / 2
        why.append("starting")
    elif starters is False and lineup_known and base >= 28:
        base -= 5
        why.append("off the bench")
    # injured teammates' minutes get shared out across the rotation, roughly by who plays most
    freed = 0.0
    for k, t in teammates.items():
        tg = [g for g in t.get("g", []) if g[3] > 0][:10]
        if not tg:
            continue
        tm = _avg([g[3] for g in tg])
        if k in out_keys:
            # only if he's been playing lately (last game within ~10 days), so long-term absences are already in the logs
            last = datetime.fromisoformat(tg[0][0])
            recent = gs and (datetime.fromisoformat(gs[0][0]) - last).days <= 10
            if tm >= 15 and recent:
                freed += tm
    if freed:
        # his share of the team's other 240 - freed minutes, 80% of it (some go to deep-bench guys)
        add = min(5.0, freed * base / max(120.0, 240.0 - freed) * 0.6)
        base += add
        why.append(f"+{add:.0f} with teammates out")
    if spread is not None and base >= 28 and abs(spread) >= 10:
        cut = 3.0 if abs(spread) >= 14 else 1.5
        base -= cut
        why.append("blowout risk")
    top = max(g[3] for g in gs[:10])   # never above his busiest recent game (+2), or 40
    return {"min": round(max(0.0, min(40.0, top + 2, base)) * 2) / 2, "l10": round(l10, 1), "l5": round(l5, 1), "why": why}


def _per36(ctx, team, b, cols):
    a = ((ctx.get("pos") or {}).get("agg") or {}).get(team, {}).get(b)
    if not a or a[0] < 200:   # need ~200 minutes at that position before we trust it
        return None
    return sum(a[COLS.index(c)] for c in cols) / a[0] * 36


def matchup(ctx, opp, pos, mk):
    """How tonight's opponent defends his position for this stat: per-36 allowed vs league average + rank.
    rank 1 = gives up the most (best matchup)."""
    b, cols = bucket(pos), STAT_COLS.get(mk)
    if not b or not cols or not opp:
        return None
    teams = ((ctx.get("pos") or {}).get("agg") or {}).keys()
    vals = {t: _per36(ctx, t, b, cols) for t in teams}
    vals = {t: v for t, v in vals.items() if v is not None}
    if opp not in vals or len(vals) < 20:
        return None
    lg = _avg(list(vals.values()))
    rank = 1 + sum(1 for v in vals.values() if v > vals[opp])
    return {"pos": b, "rank": rank, "of": len(vals), "pct": round((vals[opp] / lg - 1) * 100), "ratio": vals[opp] / lg}


def pace(ctx, team, opp):
    """Expected pace of tonight's game vs league average, e.g. +3 = 3% more possessions than usual."""
    t = ctx.get("teams") or {}
    if team not in t or opp not in t:
        return None
    lg = _avg([v["pace"] for v in t.values()])
    return round(((t[team]["pace"] + t[opp]["pace"]) / 2 / lg - 1) * 100, 1)


def describe(proj, mu, usg, pc):
    """Short text for cards and alerts."""
    bits = []
    if proj:
        bits.append(f"Proj {proj['min']:g} min (L10 {proj['l10']:g})" + (f", {', '.join(proj['why'])}" if proj["why"] else ""))
    if usg:
        bits.append(f"usage {usg:g}%")
    if mu:
        o = "most" if mu["rank"] <= mu["of"] / 2 else "fewest"
        r = mu["rank"] if o == "most" else mu["of"] + 1 - mu["rank"]
        bits.append(f"opp {r}{'tsnrhtdd'[(r//10%10!=1)*(r%10<4)*r%10::4]}-{o} to {POS_NAME[mu['pos']]} ({mu['pct']:+d}%)")
    if pc is not None and abs(pc) >= 1.5:
        bits.append(f"{'fast' if pc > 0 else 'slow'} game ({pc:+.0f}% pace)")
    return " · ".join(bits)
