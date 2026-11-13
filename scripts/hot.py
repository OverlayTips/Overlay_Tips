"""Hot stats: NBA props the player has been clearing, for the free public page (hot.html).

Published UNENCRYPTED as odds-data/hot.json, so keep it to what the public page needs.
For each player + stat we look at every over line the Aussie bookies offer (main over/unders and
ladders like 25+), keep the lines he's cleared in 80%+ of his last 10 games, and show the one that
pays the most. Ranked by last-10 hit rate, then last-5, then price.
"value" = best value picks: expected return per $1 at the best price (prob x price - 1).
prob leans mostly on the market, because bookie prices already know about minutes, injuries and
matchups that a 10-game hit rate can't see:
  market chance: Pinnacle's no-vig price at that line if it has one (sharpest), else the median no-vig
                 chance across Aussie bookies pricing both Over and Under, else the median Over price
                 with a ~10% ladder margin taken off;
  form chance:   last 5 / last 10 / season hit rates (recent games count more), shrunk toward the
                 market chance so a hot small sample can't run away with it;
  with/without: if a 20+ minute teammate is Out/Doubtful tonight, the form chance leans toward the
                 games he played without that teammate this season (3+ games each way needed);
  prob = 15-25% form + the rest market. Anything above 15% expected return is treated as a likely
  stale or wrong price and left out.
"""
from datetime import datetime, timedelta

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
ROUND5 = {"player_points_rebounds_assists"}
PRA_MIN, PRA_MAX = 1.60, 1.90
SKIP_BOOKS = {"betfair_ex_au", "pinnacle"}
MIN_GAMES, MIN_HIT, MIN_PRICE, MAX_PRICE = 6, 0.8, 1.30, 4.0
LIMIT, PER_PLAYER = 30, 2
VALUE_MIN_HIT, VALUE_MIN, VALUE_LIMIT = 0.7, 4.0, 8   # value picks: 70%+ last 10, 4%+ expected return, one per player
VALUE_BOOKS, VALUE_MAX_PRICE = 3, 3.0   # need 3+ bookies on the line; skip long shots
MARGIN_ONE_SIDED = 0.90                  # Over-only (ladder) prices: ~10% margin off the median
FORM_W = {"pin": 0.15, "two": 0.2, "one": 0.25}   # how much form counts vs the market, by market source
SHRINK = 8                               # pseudo-games pulling the form estimate toward the market
VALUE_CAP = 15.0                         # expected return above this = stale/wrong price, not value


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _book(b):
    return (b.get("title") or b["key"]).replace(" (AU)", "")


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
LEAGUE = {"basketball_nba": "NBA", "basketball_nbl": "NBL"}


def build(prop_events, stats, now, ts, out=(), sport="basketball_nba"):
    players, by, teams = stats.get("players", {}), stats.get("by", {}), stats.get("teams", {})
    cands, vcands, legs = [], [], []
    absent = _absentees(players, out)
    for ev in prop_events:
        if ev.get("sport_key") != sport:
            continue
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if start <= now:
            continue
        # best over price per (market, player, point)
        best, allp, allu = {}, {}, {}
        for b in ev.get("bookmakers", []):
            if b["key"] in SKIP_BOOKS:
                continue
            for m in b.get("markets", []):
                mk = m["key"].replace("_alternate", "")
                if mk not in STAT or mk in NO_DATA.get(sport, ()):
                    continue
                for o in m.get("outcomes", []):
                    if o.get("point") is None or not o.get("description"):
                        continue
                    k = (mk, o["description"], float(o["point"]))
                    if o.get("name") == "Under":
                        bu = allu.setdefault(k, {})
                        bu[_book(b)] = max(bu.get(_book(b), 0), o["price"])
                        continue
                    if o.get("name") != "Over":
                        continue
                    bp = allp.setdefault(k, {})
                    bp[_book(b)] = max(bp.get(_book(b), 0), o["price"])
                    if k not in best or o["price"] > best[k][0]:
                        best[k] = (o["price"], _book(b), m["key"].endswith("_alternate"))
        # Pinnacle (sharp) no-vig chance of the Over, per (market, player, point)
        pin = {}
        sb = ev.get("sharp") or {}
        for m in sb.get("markets", []):
            mk = m["key"].replace("_alternate", "")
            if mk not in STAT:
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
            gs = [g for g in p.get("g", []) if g[3] > 0]
            if len(gs) < MIN_GAMES or (now.date() - datetime.fromisoformat(gs[0][0]).date()).days > 45:
                continue   # not enough games, or nothing recent (injured / last season)
            label, f = STAT[mk]
            l10, l5 = gs[:10], gs[:5]
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
            # form chance: last 5 x3, last 10 x2, season x1, shrunk toward the market
            wh = 3 * h5 + 2 * h10 + hs
            wn = 3 * len(l5) + 2 * len(l10) + len(gs)
            wn_eff = wn / 6   # weighted games -> roughly "games of evidence"
            p_form = (wh / wn * wn_eff + SHRINK * p_mkt) / (wn_eff + SHRINK)
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
                "games": [[g[0], g[1], g[2], f(g)] for g in l10],
            }
            if wo:
                item["wo"] = wo
            legs.append({**item, "ev": ev["id"], "mk": mk})   # SGM builder (members only, see sgm.py)
            key = (desc, mk)
            if h10 / len(l10) >= MIN_HIT and (key not in top or price > top[key]["price"]):
                top[key] = item
            if (VALUE_MIN <= item["value"] <= VALUE_CAP and len(ps) >= VALUE_BOOKS and price <= VALUE_MAX_PRICE
                    and price <= med * 1.25) and (key not in vtop or item["value"] > vtop[key]["value"]):
                vtop[key] = item
        cands += top.values()
        vcands += vtop.values()
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
    return {"updated": ts, "items": out, "value": value, "_legs": legs}
