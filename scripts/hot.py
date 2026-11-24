"""Hot stats: NBA props the player has been clearing, for the free public page (hot.html).

Published UNENCRYPTED as odds-data/hot.json, so keep it to what the public page needs.
For each player + stat we look at every over line the Aussie bookies offer (main over/unders and
ladders like 25+), keep the lines he's cleared in 80%+ of his last 15 games, and show the one that
pays the most. Ranked by last-15 hit rate, then last-5, then price.
"cold" = cold streaks: main-line UNDERS the player has stayed under in 80%+ of his last 15 (cold.html).
"value" = best value picks: expected return per $1 at the best price (prob x price - 1).
prob leans mostly on the market, because bookie prices already know about minutes, injuries and
matchups that a 15-game hit rate can't see:
  market chance: Pinnacle's no-vig price at that line if it has one (sharpest), else the median no-vig
                 chance across Aussie bookies pricing both Over and Under, else the median Over price
                 with a ~10% ladder margin taken off;
  form chance:   last 5 / last 15 / season hit rates (recent games count more), shrunk toward the
                 market chance so a hot small sample can't run away with it;
  with/without: if a 20+ minute teammate is Out/Doubtful tonight, the form chance leans toward the
                 games he played without that teammate this season (3+ games each way needed);
  prob = 15-25% form + the rest market. Anything above 15% expected return is treated as a likely
  stale or wrong price and left out.
"""
import math, os
from datetime import datetime, timedelta

# Best price only from bookies we hold (same setting as picks.py); other bookies still shape the market chance
MY_BOOKS = {b.strip() for b in os.environ.get("MY_BOOKS", "").split(",") if b.strip()}

STAT = {  # game row: [date, opp, '@'|'vs', min, pts, reb, ast, 3pm, blk, stl, tov]
    "player_points": ("Points", lambda g: g[4]),
    "player_rebounds": ("Rebounds", lambda g: g[5]),
    "player_assists": ("Assists", lambda g: g[6]),
    "player_threes": ("Threes", lambda g: g[7]),
    "player_blocks": ("Blocks", lambda g: g[8]),
    "player_steals": ("Steals", lambda g: g[9]),
    "player_points_rebounds_assists": ("Pts + Reb + Ast", lambda g: g[4] + g[5] + g[6]),
    "player_points_rebounds": ("Pts + Reb", lambda g: g[4] + g[5]),
    "player_points_assists": ("Pts + Ast", lambda g: g[4] + g[6]),
    "player_rebounds_assists": ("Reb + Ast", lambda g: g[5] + g[6]),
}
# NFL (nfl_stats.py) game row: [date, opp, '@'|'vs', 1, pyd, ptd, cmp, patt, int, ryd, ratt, rec, ryds, tgt]
NFL_STAT = {
    "player_pass_yds": ("Passing Yards", lambda g: g[4]),
    "player_pass_tds": ("Passing TDs", lambda g: g[5]),
    "player_pass_completions": ("Completions", lambda g: g[6]),
    "player_pass_attempts": ("Pass Attempts", lambda g: g[7]),
    "player_rush_yds": ("Rushing Yards", lambda g: g[9]),
    "player_rush_attempts": ("Rush Attempts", lambda g: g[10]),
    "player_receptions": ("Receptions", lambda g: g[11]),
    "player_reception_yds": ("Receiving Yards", lambda g: g[12]),
}
ROUND5 = {"player_points_rebounds_assists"}
NFL_USE = {   # which workload number drives each NFL stat: (row index, label)
    "player_pass_yds": (7, "pass attempts"), "player_pass_tds": (7, "pass attempts"),
    "player_pass_completions": (7, "pass attempts"), "player_pass_attempts": (7, "pass attempts"),
    "player_rush_yds": (10, "carries"), "player_rush_attempts": (10, "carries"),
    "player_receptions": (13, "targets"), "player_reception_yds": (13, "targets"),
}
NFL_POS = {"FB": "RB"}


def _nfl_defence(players, mk, f):
    """{(opp team, position): (avg stat allowed per game, rank, of)} from every logged player's games."""
    per = {}   # (opp, pos) -> {(date, player's team): total}
    for p in players.values():
        pos = NFL_POS.get(p.get("pos"), p.get("pos"))
        if pos not in ("QB", "RB", "WR", "TE"):
            continue
        for g in p.get("g", []):
            k = (g[1], pos)
            gk = (g[0], p.get("team"))
            per.setdefault(k, {})[gk] = per.setdefault(k, {}).get(gk, 0) + f(g)
    avg = {k: sum(v.values()) / len(v) for k, v in per.items() if len(v) >= 3}
    out = {}
    for pos in ("QB", "RB", "WR", "TE"):
        ks = sorted((k for k in avg if k[1] == pos), key=lambda k: -avg[k])
        lg = sum(avg[k] for k in ks) / len(ks) if ks else 0
        for i, k in enumerate(ks):
            out[k] = (round(avg[k], 1), i + 1, len(ks), round(100 * (avg[k] / lg - 1)) if lg else 0)
    return out


def nfl_ctx(players, p, gs, mk, f, opp, gm=None, cache=None):
    """NFL write-up context for one player + market: {"use", "def", "gm"} (any may be missing). Best effort."""
    try:
        out = {}
        ui, ul = NFL_USE[mk]
        us = sum(g[ui] for g in gs) / len(gs)
        out["use"] = {"what": ul, "l3": round(sum(g[ui] for g in gs[:3]) / min(3, len(gs)), 1), "season": round(us, 1)}
        if ui == 13:
            tg = sum(g[13] for g in gs)
            if tg:
                out["use"]["catch"] = round(100 * sum(g[11] for g in gs) / tg)
        pos = NFL_POS.get(p.get("pos"), p.get("pos"))
        cache = {} if cache is None else cache
        if mk not in cache:
            cache[mk] = _nfl_defence(players, mk, f)
        dv = cache[mk].get((opp, pos))
        if dv and opp:
            out["def"] = {"opp": opp, "pos": pos, "avg": dv[0], "rank": dv[1], "of": dv[2], "pct": dv[3]}
        if gm:
            out["gm"] = {"spread": gm[0], "total": gm[1], "tt": round(gm[1] / 2 - gm[0] / 2, 1)}
        return out
    except Exception:
        return None


def _nfl_lines(ev):
    """(spread by team name, game total) from the bookies' main lines (Pinnacle first), or ({}, None)."""
    src = [ev["sharp"]] if ev.get("sharp") else ev.get("bookmakers", [])
    sp, tot = {}, []
    for b in src:
        for m in b.get("markets", []):
            for o in m.get("outcomes", []):
                if o.get("point") is None:
                    continue
                if m["key"] == "spreads":
                    sp.setdefault(o["name"], []).append(o["point"])
                elif m["key"] == "totals" and o.get("name") == "Over":
                    tot.append(o["point"])
    return {n: _median(sorted(v)) for n, v in sp.items()}, (_median(sorted(tot)) if tot else None)
PRA_MIN, PRA_MAX = 1.60, 1.90
SKIP_BOOKS = {"betfair_ex_au", "pinnacle"}
MIN_GAMES, MIN_HIT, MIN_PRICE, MAX_PRICE = 6, 0.8, 1.30, 4.0
FORM_N = 15   # hit-rate window: last 15 games (the "l10" keys in the JSON hold these, name kept for the pages)
LIMIT, PER_PLAYER = 30, 2
VALUE_MIN_HIT, VALUE_MIN, VALUE_LIMIT = 0.7, 4.0, 8   # value picks: 70%+ last 15, 4%+ expected return, one per player
VALUE_BOOKS, VALUE_MAX_PRICE = 3, 3.0   # need 3+ bookies on the line; skip long shots
MARGIN_ONE_SIDED = 0.90                  # Over-only (ladder) prices: ~10% margin off the median
FORM_W = {"pin": 0.15, "two": 0.2, "one": 0.25}   # how much form counts vs the market, by market source
SHRINK = 8                               # pseudo-games pulling the form estimate toward the market
VALUE_CAP = 15.0                         # expected return above this = stale/wrong price, not value
# NBL props at Aussie bookies are nearly all Over-only ladders (no Unders, no Pinnacle), so a two-sided-only rule
# left NBL with almost no value alerts. There, one-sided lines count too, on a stricter bar: more bookies, bigger edge.
ONE_SIDED_OK = {"basketball_nbl"}
ONE_SIDED_BOOKS, ONE_SIDED_MIN = 4, 6.0


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _book(b):
    return (b.get("title") or b["key"]).replace(" (AU)", "")


def team_games(p, gs, h, a, sport="basketball_nba"):
    """His games for his CURRENT team only, so a trade doesn't leave his old role in the form numbers.
    Returns (games, move): move = {"from": old team, "g": games with the new one} after a recent move, else None.
    games is cut to the new-team games, so he drops out until he has enough of them (callers' min-games check).
    Signals, in order: his logged team isn't playing in this game at all (traded, no games for the new side yet);
    the team tag on each game row (last element, newer logs); the stats file's move record ("mv", NFL)."""
    cur = p.get("team")
    if sport not in ("basketball_nba", "americanfootball_nfl"):
        return gs, None   # NBL logs name teams differently; not checked
    if h and a and cur and cur not in (h, a):
        return [], {"from": cur, "g": 0}
    n = 0
    for g in gs[:FORM_N]:
        t = g[-1] if isinstance(g[-1], str) and g[-1] else None
        if t is None:
            break   # untagged (older logs): can't tell from the rows
        if t != cur:
            return gs[:n], {"from": t, "g": n}
        n += 1
    mv = p.get("mv") or {}
    if mv.get("to") == cur and mv.get("d"):
        n = sum(1 for g in gs if g[0] >= mv["d"])
        if n < FORM_N:
            return gs[:n], {"from": mv.get("from"), "g": n}
    return gs, None


ROLE_MIN_GAMES = 6      # need this many games in the new role before the form counts only those
ROLE_MIN_SHIFT = 6.0    # minutes a game: the size of change that counts as a new role (and 20%+)


def role_window(gs, sport="basketball_nba"):
    """His form window (last FORM_N games), cut back to just the games since a role change on the same team:
    e.g. moved into the starting five three weeks ago, or lost his starting spot. Without this, the older
    games in the window come from a different role and skew the hit rate.
    A role change = his minutes in the newest k games (k >= ROLE_MIN_GAMES) are clearly different from the
    games before them (6+ min and 20%+), and that holds game by game (at least 80% of the new games sit on
    the new side), not just one big night. Picks the longest such run so the sample stays as big as possible.
    Returns (games, role): role = {"from": old avg min, "to": new avg min, "g": games in the new role} or None.
    Basketball only (NFL logs have no minutes)."""
    w = gs[:FORM_N]
    if sport not in ("basketball_nba", "basketball_nbl") or len(w) < ROLE_MIN_GAMES + 3:
        return w, None
    mins = [g[3] for g in w]
    avg = lambda xs: sum(xs) / len(xs)
    best = None
    for k in range(ROLE_MIN_GAMES, len(w) - 2):   # 3+ old-role games to compare against
        new, old = avg(mins[:k]), avg(mins[k:])
        if abs(new - old) < max(ROLE_MIN_SHIFT, 0.2 * old):
            continue
        mid, up = (new + old) / 2, new > old
        side = sum(1 for m in mins[:k] if (m > mid) == up)
        if side < 0.8 * k:
            continue
        # the cleanest split: most games on the right side of it (new role before, old role after); ties -> longer
        fit = side + sum(1 for m in mins[k:] if (m > mid) != up)
        if best is None or fit >= best[0]:
            best = (fit, k, old, new)
    if not best:
        return w, None
    _, k, old, new = best
    return w[:k], {"from": round(old, 1), "to": round(new, 1), "g": k}


WO_MIN_MINS, WO_MIN_GAMES = 20, 3   # teammate counts if he averages 20+ min over 3+ games this season
WO_PRIOR = 6                        # pseudo-games pulling the "without him" hit rate toward normal form


def _absentees(players, out):
    """{team: (name, avg mins, set of dates he played)} for the biggest-minutes player missing tonight."""
    try:
        from player_stats import norm
    except Exception:
        return {}
    best = {}
    for n in out or ():
        p = players.get(norm(n))
        gs = [g for g in (p or {}).get("g", []) if g[3] > 0]
        if not p or len(gs) < WO_MIN_GAMES:
            continue
        mins = sum(g[3] for g in gs) / len(gs)
        if mins < WO_MIN_MINS:
            continue
        t = p.get("team")
        if t and (t not in best or mins > best[t][1]):
            best[t] = (p.get("n") or n, mins, {g[0] for g in gs}, min(g[0] for g in gs))
    return best


NO_DATA = {"basketball_nbl": {"player_blocks", "player_steals"}}   # API-Basketball has no blocks/steals
LEAGUE = {"basketball_nba": "NBA", "basketball_nbl": "NBL", "americanfootball_nfl": "NFL"}


def _spreads(games, teams):
    """{event id: {team abbr: spread}} from the game lines (Pinnacle if it has one, else the median bookie line)."""
    out = {}
    for ev in games or ():
        src = [ev["sharp"]] if ev.get("sharp") else ev.get("bookmakers", [])
        pts = {}
        for b in src:
            for m in b.get("markets", []):
                if m["key"] != "spreads":
                    continue
                for o in m.get("outcomes", []):
                    if o.get("point") is not None:
                        pts.setdefault(o["name"], []).append(o["point"])
        out[ev["id"]] = {teams.get(n): _median(sorted(v)) for n, v in pts.items() if teams.get(n)}
    return out


def build(prop_events, stats, now, ts, out=(), sport="basketball_nba", ctx=None, games=(), avail=None):
    players, by, teams = stats.get("players", {}), stats.get("by", {}), stats.get("teams", {})
    cands, vcands, legs, ccands = [], [], [], []
    nfl = sport == "americanfootball_nfl"
    ST = NFL_STAT if nfl else STAT
    dcache = {}
    glines = {ev["id"]: _nfl_lines(ev) for ev in games or ()} if nfl else {}
    absent = _absentees(players, out)
    # role + matchup context (NBA only; see matchups.py)
    mx = None
    if sport == "basketball_nba" and ctx:
        try:
            import matchups as mx
            from player_stats import norm as _norm
            from injuries import key as _ikey
        except Exception:
            mx = None
    if mx:
        out_keys = {_norm(n) for n in out or ()}
        by_team = {}
        for k, pl in players.items():
            by_team.setdefault(pl.get("team"), {})[k] = pl
        spreads = _spreads(games, teams)
        posmap = {k: v[3] for k, v in ((stats.get("ids") or {}).get("map") or {}).items() if len(v) > 3}
        proj_cache = {}

    def project(p, desc, ev):
        """Projected minutes tonight (cached per player): recent minutes, starting role, teammates out, blowout risk."""
        if not mx:
            return None
        pk = by.get(desc, "")
        if pk not in proj_cache:
            tm = p.get("team")
            mates = {k: v for k, v in by_team.get(tm, {}).items() if k != pk}
            st = None
            if avail and tm in avail.lineup_teams:
                st = _ikey(p.get("n") or desc) in avail.proj_starters
            proj_cache[pk] = mx.minutes(p, mates, out_keys, st, bool(avail and tm in avail.lineup_teams),
                                        spreads.get(ev["id"], {}).get(tm))
        return proj_cache[pk]

    def role_up(p, gs, proj):
        """Why an Under is a pass tonight, or None: his role looks bigger than the games the under record is built on."""
        if not mx:
            return None
        if proj:
            if "starting" in proj["why"]:
                return "starting tonight, usually off the bench"
            if proj["min"] >= proj["l10"] + 3 and proj["min"] >= proj["l10"] * 1.12:
                return f"projects {proj['min']:g} min, up from {proj['l10']:g}"
        # last game was a minutes spike with teammates missing, and those teammates are out again tonight
        prev = [g[3] for g in gs[1:10]]
        if gs and prev and gs[0][3] >= 1.35 * sum(prev) / len(prev):
            last = gs[0][0]
            for k, t in by_team.get(p.get("team"), {}).items():
                tg = [g for g in t.get("g", []) if g[3] > 0][:10]
                if k in out_keys and tg and sum(g[3] for g in tg) / len(tg) >= 15 and last not in {g[0] for g in t.get("g", [])}:
                    return f"played {gs[0][3]:.0f} min last game with {t.get('n') or k} out, and he's out again"
        return None

    for ev in prop_events:
        if ev.get("sport_key") != sport:
            continue
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if start <= now:
            continue
        # best over price per (market, player, point)
        best, allp, allu, bestu = {}, {}, {}, {}
        for b in ev.get("bookmakers", []):
            if b["key"] in SKIP_BOOKS:
                continue
            for m in b.get("markets", []):
                mk = m["key"].replace("_alternate", "")
                if mk not in ST or mk in NO_DATA.get(sport, ()):
                    continue
                for o in m.get("outcomes", []):
                    if o.get("point") is None or not o.get("description"):
                        continue
                    k = (mk, o["description"], float(o["point"]))
                    if o.get("name") == "Under":
                        bu = allu.setdefault(k, {})
                        bu[_book(b)] = max(bu.get(_book(b), 0), o["price"])
                        if (not MY_BOOKS or b["key"] in MY_BOOKS) and not m["key"].endswith("_alternate") \
                                and (k not in bestu or o["price"] > bestu[k][0]):
                            bestu[k] = (o["price"], _book(b))
                        continue
                    if o.get("name") != "Over":
                        continue
                    bp = allp.setdefault(k, {})
                    bp[_book(b)] = max(bp.get(_book(b), 0), o["price"])
                    if MY_BOOKS and b["key"] not in MY_BOOKS:
                        continue   # still counts toward the market price, but we can't bet it there
                    if k not in best or o["price"] > best[k][0]:
                        best[k] = (o["price"], _book(b), m["key"].endswith("_alternate"))
        # Pinnacle (sharp) no-vig chance of the Over, per (market, player, point)
        pin = {}
        sb = ev.get("sharp") or {}
        for m in sb.get("markets", []):
            mk = m["key"].replace("_alternate", "")
            if mk not in ST:
                continue
            pr = {}
            for o in m.get("outcomes", []):
                if o.get("point") is not None and o.get("description"):
                    pr.setdefault((mk, o["description"], float(o["point"])), {})[o.get("name")] = o["price"]
            for k, sides in pr.items():
                if "Over" in sides and "Under" in sides:
                    io, iu = 1 / sides["Over"], 1 / sides["Under"]
                    pin[k] = io / (io + iu)
        h, a = teams.get(ev["home_team"]), teams.get(ev["away_team"])
        top, vtop = {}, {}
        for (mk, desc, pt), (price, book, ladder) in best.items():
            if not (MIN_PRICE <= price <= MAX_PRICE):
                continue
            if mk in ROUND5 and int(pt + 0.5) % 5 and not (PRA_MIN <= price <= PRA_MAX):
                continue   # PRA: 15+, 20+, 25+, 30+ ... plus any line at $1.60-$1.90
            p = players.get(by.get(desc, ""))
            if not p:
                continue
            gs, moved = team_games(p, [g for g in p.get("g", []) if g[3] > 0], h, a, sport)
            if len(gs) < (4 if sport == "americanfootball_nfl" else MIN_GAMES) or (now.date() - datetime.fromisoformat(gs[0][0]).date()).days > 45:
                continue   # not enough games, or nothing recent (injured / last season)
            label, f = ST[mk]
            l10, role = role_window(gs, sport)   # last 15, or just the games since a role change
            l5 = gs[:5]
            hit = lambda xs: sum(1 for g in xs if f(g) > pt)
            h10, h5, hs = hit(l10), hit(l5), hit(gs)
            # with/without: his games since the injured teammate's first game this season, split by
            # whether that teammate played (a usage bump the bookies may not have priced in yet)
            wo, ab = None, absent.get(p.get("team"))
            if ab and ab[0] != (p.get("n") or desc):
                since = [g for g in gs if g[0] >= ab[3]]
                gw = [g for g in since if g[0] not in ab[2]]
                gm = [g for g in since if g[0] in ab[2]]
                if len(gw) >= WO_MIN_GAMES and len(gm) >= WO_MIN_GAMES:
                    wo = {"n": ab[0], "g": len(gw), "hit": [hit(gw), len(gw)],
                          "avg": round(sum(f(g) for g in gw) / len(gw), 1),
                          "with": round(sum(f(g) for g in gm) / len(gm), 1)}
            if h10 / len(l10) < VALUE_MIN_HIT and not (wo and wo["hit"][0] / wo["hit"][1] >= VALUE_MIN_HIT):
                continue
            opp = a if p.get("team") == h else h if p.get("team") == a else None
            vs = [g for g in gs if opp and g[1] == opp]
            ps = sorted(allp[(mk, desc, pt)].values())
            med = _median(ps)
            # market chance: sharp first, then two-sided Aussie consensus, then one-sided with a margin off
            k3 = (mk, desc, pt)
            ov, un = allp.get(k3, {}), allu.get(k3, {})
            both = [(1 / ov[bk]) / (1 / ov[bk] + 1 / un[bk]) for bk in ov if bk in un]
            if k3 in pin:
                p_mkt, src = pin[k3], "pin"
            elif len(both) >= 3:
                p_mkt, src = _median(sorted(both)), "two"
            else:
                p_mkt, src = min(0.95, MARGIN_ONE_SIDED / med), "one"
            # form chance: last 5 x3, last 15 x2, season x1, shrunk toward the market
            wh = 3 * h5 + 2 * h10 + hs
            wn = 3 * len(l5) + 2 * len(l10) + len(gs)
            wn_eff = wn / 6   # weighted games -> roughly "games of evidence"
            p_form = (wh / wn * wn_eff + SHRINK * p_mkt) / (wn_eff + SHRINK)
            proj = mu = pc = usg = None
            if mx:
                pk = by.get(desc, "")
                proj = project(p, desc, ev)
                mu = mx.matchup(ctx, opp, posmap.get(pk), mk)
                pc = mx.pace(ctx, p.get("team"), opp)
                usg = (ctx.get("usg") or {}).get(str(p.get("id")))
                # minutes x per-minute rate x matchup x pace -> chance of clearing the line, blended into form
                mins10 = sum(g[3] for g in l10)
                if proj and mins10 > 0:
                    mean = sum(f(g) for g in l10) / mins10 * proj["min"]
                    if mu:
                        mean *= max(0.85, min(1.15, 1 + 0.5 * (mu["ratio"] - 1)))
                    if pc is not None:
                        mean *= 1 + 0.5 * pc / 100
                    vals = [f(g) for g in l10]
                    avgv = sum(vals) / len(vals)
                    sd = max((sum((v - avgv) ** 2 for v in vals) / max(1, len(vals) - 1)) ** 0.5, 0.9 * math.sqrt(max(mean, 0.5)), 0.8)
                    p_model = 1 - 0.5 * (1 + math.erf((pt - mean) / sd / math.sqrt(2)))
                    p_form = 0.5 * p_form + 0.5 * min(0.97, max(0.03, p_model))
            if wo:   # tonight he plays without that teammate: lean the form chance toward those games
                p_form = (wo["hit"][0] + WO_PRIOR * p_form) / (wo["hit"][1] + WO_PRIOR)
            p_hit = (h10 + hs + 1) / (len(l10) + len(gs) + 2)   # shown on the card as "Form"
            w = FORM_W[src]
            prob = w * p_form + (1 - w) * p_mkt
            item = {
                "player": p.get("n") or desc, "team": p.get("team"), "opp": opp, "lg": LEAGUE.get(sport, ""),
                "game": f"{ev['away_team']} @ {ev['home_team']}", "start": ev["commence_time"],
                "stat": label, "line": f"{int(pt + 0.5)}+", "point": pt, "price": price, "book": book,
                "l10": [h10, len(l10)], "l5": [h5, len(l5)], "vs": [hit(vs), len(vs)], "season": [hs, len(gs)],
                "avg": round(sum(f(g) for g in l10) / len(l10), 1),
                "prob": round(prob, 3), "hit": round(p_hit, 3), "mkt": round(p_mkt, 3), "msrc": src, "books": len(ps), "value": round((prob * price - 1) * 100, 1),
                "prices": dict(sorted(allp[(mk, desc, pt)].items(), key=lambda x: -x[1])),
                "games": [[g[0], g[1], g[2], f(g), round(g[3]) if sport != "americanfootball_nfl" else None] for g in l10],   # 5th = minutes (write-up workload)
            }
            if moved:
                item["moved"] = moved
            if role:
                item["role"] = role
            if nfl:   # NFL context for the write-up: workload trend, what the defence allows his position, game script
                spr, tot = glines.get(ev["id"], ({}, None))
                me = ev["home_team"] if p.get("team") == h else ev["away_team"] if p.get("team") == a else None
                c = nfl_ctx(players, p, gs, mk, f, opp, (spr[me], tot) if me in spr and tot else None, dcache)
                if c:
                    item["nfl"] = c
            # last meetings with tonight's opponent, this season then last season (newest first)
            meet = [g for g in gs if opp and g[1] == opp] + [g for g in p.get("p", []) if opp and g[1] == opp and g[3] > 0]
            if meet:
                item["h2h"] = [[g[0], g[1], g[2], f(g)] for g in meet[:3]]
            if wo:
                item["wo"] = wo
            if proj:
                item["proj"] = proj
                item["mean"] = round(mean, 1) if mins10 > 0 else None
            if mu:
                item["mu"] = {k: mu[k] for k in ("pos", "rank", "of", "pct")}
            if pc is not None:
                item["pace"] = pc
            if usg:
                item["usg"] = usg
            if mx and (proj or mu or usg):
                item["ctx"] = mx.describe(proj, mu, usg, pc)
            legs.append({**item, "ev": ev["id"], "mk": mk})   # SGM builder (members only, see sgm.py)
            key = (desc, mk)
            if h10 / len(l10) >= MIN_HIT and (key not in top or price > top[key]["price"]):
                top[key] = item
            item.update({"ev": ev["id"], "mk": mk, "desc": desc, "sk": sport})   # lets the bet log re-find this line for its closing price
            # value needs a real two-sided fair price: an Over-only ladder with a guessed margin isn't one
            if src == "one":
                vok = sport in ONE_SIDED_OK and ONE_SIDED_MIN <= item["value"] <= VALUE_CAP and len(ps) >= ONE_SIDED_BOOKS
            else:
                vok = VALUE_MIN <= item["value"] <= VALUE_CAP and len(ps) >= VALUE_BOOKS
            if (vok and price <= VALUE_MAX_PRICE and price <= med * 1.25) and (key not in vtop or item["value"] > vtop[key]["value"]):
                vtop[key] = item
        cands += top.values()
        vcands += vtop.values()
        def enrich(item, p, gs, mk, f, opp, desc, ev=ev, h=h, a=a):   # same context as the Hot stats cards, for the write-up
            try:
                if nfl:
                    spr, tot = glines.get(ev["id"], ({}, None))
                    me = ev["home_team"] if p.get("team") == h else ev["away_team"] if p.get("team") == a else None
                    c = nfl_ctx(players, p, gs, mk, f, opp, (spr[me], tot) if me in spr and tot else None, dcache)
                    if c:
                        item["nfl"] = c
                elif mx:
                    pk = by.get(desc, "")
                    mu = mx.matchup(ctx, opp, posmap.get(pk), mk)
                    pc = mx.pace(ctx, p.get("team"), opp)
                    usg = (ctx.get("usg") or {}).get(str(p.get("id")))
                    if mu:
                        item["mu"] = {k: mu[k] for k in ("pos", "rank", "of", "pct")}
                    if pc is not None:
                        item["pace"] = pc
                    if usg:
                        item["usg"] = usg
                    if pk in proj_cache and proj_cache[pk]:
                        item["proj"] = proj_cache[pk]
                    if mu or usg or pc is not None:
                        item["ctx"] = mx.describe(proj_cache.get(pk), mu, usg, pc)
            except Exception:
                pass
        ccands += _cold(ev, bestu, allp, allu, pin, players, by, teams, ST, sport, now, enrich,
                        (lambda p, d, ev=ev: project(p, d, ev)) if mx else None, role_up if mx else None)
    cands.sort(key=lambda x: (x["l10"][0] / x["l10"][1], x["l5"][0] / x["l5"][1], x["price"]), reverse=True)
    out, per = [], {}
    for c in cands:
        if per.get(c["player"], 0) >= PER_PLAYER:
            continue
        per[c["player"]] = per.get(c["player"], 0) + 1
        out.append(c)
        if len(out) >= LIMIT:
            break
    vcands.sort(key=lambda x: x["value"], reverse=True)
    value, seen = [], set()
    for c in vcands:
        if c["player"] in seen:
            continue
        seen.add(c["player"])
        value.append(c)
        if len(value) >= VALUE_LIMIT:
            break
    ccands.sort(key=lambda x: (x["l10"][0] / x["l10"][1], x["l5"][0] / x["l5"][1], x["value"]), reverse=True)
    cold, per = [], {}
    for c in ccands:
        if per.get(c["player"], 0) >= PER_PLAYER:
            continue
        per[c["player"]] = per.get(c["player"], 0) + 1
        cold.append(c)
        if len(cold) >= LIMIT:
            break
    return {"updated": ts, "items": out, "value": value, "cold": cold, "_legs": legs}


COLD_MAX_PRICE = 3.0


def _cold(ev, bestu, allp, allu, pin, players, by, teams, ST, sport, now, enrich=None, project=None, role_up=None):
    """Cold streaks: main-line unders the player has stayed under in 80%+ of his last 15.
    Needs a two-sided market (Pinnacle, or 3+ Aussie bookies pricing Over and Under) for a fair price.
    NBA: skipped when his role looks bigger tonight (starting, minutes up, or a minutes spike last game with the
    same teammates out again), since the under record comes from his normal role. Otherwise the chance blends in
    projected minutes x his per-minute rate, like the Overs."""
    h, a = teams.get(ev["home_team"]), teams.get(ev["away_team"])
    top = {}
    for (mk, desc, pt), (price, book) in bestu.items():
        if not (MIN_PRICE <= price <= COLD_MAX_PRICE):
            continue
        p = players.get(by.get(desc, ""))
        if not p:
            continue
        gs, moved = team_games(p, [g for g in p.get("g", []) if g[3] > 0], h, a, sport)
        if len(gs) < (4 if sport == "americanfootball_nfl" else MIN_GAMES) or (now.date() - datetime.fromisoformat(gs[0][0]).date()).days > 45:
            continue
        label, f = ST[mk]
        l10, role = role_window(gs, sport)
        l5 = gs[:5]
        under = lambda xs: sum(1 for g in xs if f(g) < pt)
        u10, u5, us = under(l10), under(l5), under(gs)
        if u10 / len(l10) < MIN_HIT:
            continue
        k3 = (mk, desc, pt)
        ov, un = allp.get(k3, {}), allu.get(k3, {})
        both = [(1 / un[bk]) / (1 / ov[bk] + 1 / un[bk]) for bk in un if bk in ov]
        if k3 in pin:
            p_mkt, src = 1 - pin[k3], "pin"
        elif len(both) >= 3:
            p_mkt, src = _median(sorted(both)), "two"
        else:
            continue   # no honest fair price for the under
        wh = 3 * u5 + 2 * u10 + us
        wn = 3 * len(l5) + 2 * len(l10) + len(gs)
        wn_eff = wn / 6
        p_form = (wh / wn * wn_eff + SHRINK * p_mkt) / (wn_eff + SHRINK)
        proj = project(p, desc) if project else None
        if role_up and role_up(p, gs, proj):
            print(f"cold: pass {desc} Under {pt:g} {mk}: {role_up(p, gs, proj)}")
            continue
        mins10 = sum(g[3] for g in l10)
        if proj and mins10 > 0:   # projected minutes x per-minute rate -> chance of staying under
            mean = sum(f(g) for g in l10) / mins10 * proj["min"]
            vals = [f(g) for g in l10]
            avgv = sum(vals) / len(vals)
            sd = max((sum((v - avgv) ** 2 for v in vals) / max(1, len(vals) - 1)) ** 0.5, 0.9 * math.sqrt(max(mean, 0.5)), 0.8)
            p_under = 0.5 * (1 + math.erf((pt - mean) / sd / math.sqrt(2)))
            p_form = 0.5 * p_form + 0.5 * min(0.97, max(0.03, p_under))
        prob = FORM_W[src] * p_form + (1 - FORM_W[src]) * p_mkt
        opp = a if p.get("team") == h else h if p.get("team") == a else None
        vs = [g for g in gs if opp and g[1] == opp]
        item = {
            "player": p.get("n") or desc, "team": p.get("team"), "opp": opp, "lg": LEAGUE.get(sport, ""),
            "game": f"{ev['away_team']} @ {ev['home_team']}", "start": ev["commence_time"],
            "stat": label, "side": "Under", "line": f"Under {pt:g}", "point": pt, "price": price, "book": book,
            "l10": [u10, len(l10)], "l5": [u5, len(l5)], "vs": [under(vs), len(vs)], "season": [us, len(gs)],
            "avg": round(sum(f(g) for g in l10) / len(l10), 1),
            "prob": round(prob, 3), "mkt": round(p_mkt, 3), "msrc": src, "books": len(un),
            "value": round((prob * price - 1) * 100, 1),
            "prices": dict(sorted(un.items(), key=lambda x: -x[1])),
            "games": [[g[0], g[1], g[2], f(g), None] for g in l10],
        }
        if moved:
            item["moved"] = moved
        if role:
            item["role"] = role
        meet = [g for g in gs if opp and g[1] == opp] + [g for g in p.get("p", []) if opp and g[1] == opp and g[3] > 0]
        if meet:
            item["h2h"] = [[g[0], g[1], g[2], f(g)] for g in meet[:3]]
        if enrich:
            enrich(item, p, gs, mk, f, opp, desc)
        key = (desc, mk)
        if key not in top or price > top[key]["price"]:
            top[key] = item
    return list(top.values())
