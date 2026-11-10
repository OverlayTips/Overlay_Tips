"""Hot stats: NBA props the player has been clearing, for the free public page (hot.html).

Published UNENCRYPTED as odds-data/hot.json, so keep it to what the public page needs.
For each player + stat we look at every over line the Aussie bookies offer (main over/unders and
ladders like 25+), keep the lines he's cleared in 80%+ of his last 10 games, and show the one that
pays the most. Ranked by last-10 hit rate, then last-5, then price.
"value" = best value picks: where his hit rate beats the chance the price implies by the most
(prob x price - 1, with prob from his last 10 + season, nudged toward 50% for small samples).
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
SKIP_BOOKS = {"betfair_ex_au", "pinnacle"}
MIN_GAMES, MIN_HIT, MIN_PRICE, MAX_PRICE = 6, 0.8, 1.30, 4.0
LIMIT, PER_PLAYER = 30, 2
VALUE_MIN_HIT, VALUE_MIN, VALUE_LIMIT = 0.6, 8.0, 8   # value picks: 60%+ last 10, 8%+ expected return, one per player


def _book(b):
    return (b.get("title") or b["key"]).replace(" (AU)", "")


def build(prop_events, stats, now, ts):
    players, by, teams = stats.get("players", {}), stats.get("by", {}), stats.get("teams", {})
    cands, vcands = [], []
    for ev in prop_events:
        if ev.get("sport_key") != "basketball_nba":
            continue
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if start <= now:
            continue
        # best over price per (market, player, point)
        best = {}
        for b in ev.get("bookmakers", []):
            if b["key"] in SKIP_BOOKS:
                continue
            for m in b.get("markets", []):
                mk = m["key"].replace("_alternate", "")
                if mk not in STAT:
                    continue
                for o in m.get("outcomes", []):
                    if o.get("name") != "Over" or o.get("point") is None or not o.get("description"):
                        continue
                    k = (mk, o["description"], float(o["point"]))
                    if k not in best or o["price"] > best[k][0]:
                        best[k] = (o["price"], _book(b), m["key"].endswith("_alternate"))
        h, a = teams.get(ev["home_team"]), teams.get(ev["away_team"])
        top, vtop = {}, {}
        for (mk, desc, pt), (price, book, ladder) in best.items():
            if not (MIN_PRICE <= price <= MAX_PRICE):
                continue
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
            if h10 / len(l10) < VALUE_MIN_HIT:
                continue
            opp = a if p.get("team") == h else h if p.get("team") == a else None
            vs = [g for g in gs if opp and g[1] == opp]
            # chance he clears it: last 10 counted twice (once on its own, once in the season), pulled
            # a little toward 50% so a perfect small sample doesn't read as a sure thing
            prob = (h10 + hs + 1) / (len(l10) + len(gs) + 2)
            item = {
                "player": p.get("n") or desc, "team": p.get("team"), "opp": opp,
                "game": f"{ev['away_team']} @ {ev['home_team']}", "start": ev["commence_time"],
                "stat": label, "line": f"{int(pt + 0.5)}+", "point": pt, "price": price, "book": book,
                "l10": [h10, len(l10)], "l5": [h5, len(l5)], "vs": [hit(vs), len(vs)], "season": [hs, len(gs)],
                "avg": round(sum(f(g) for g in l10) / len(l10), 1),
                "prob": round(prob, 3), "value": round((prob * price - 1) * 100, 1),
                "games": [[g[0], g[1], g[2], f(g)] for g in l10],
            }
            key = (desc, mk)
            if h10 / len(l10) >= MIN_HIT and (key not in top or price > top[key]["price"]):
                top[key] = item
            if item["value"] >= VALUE_MIN and (key not in vtop or item["value"] > vtop[key]["value"]):
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
    return {"updated": ts, "items": out, "value": value}
