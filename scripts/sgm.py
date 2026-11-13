"""Value SGMs for the members' odds tracker (bundle["sgm"]).

Legs come from hot.py: NBA overs the player has cleared in 70%+ of his last 10, each with our rated
chance (form + bookie consensus). An SGM has to be at ONE bookie, so for every game and bookie we
take the legs that bookie prices and look for 2-3 leg combos where:
  - every leg is value on its own at that bookie (our chance x price >= 1)
  - different players, max 2 legs per team, never two scoring legs (points / PRA / P+R / P+A) from
    the same team (they eat into each other), no same-team stacking of the same stat
  - the multi (legs multiplied) lands in the 1.70-2.30 range
"Fair" = 1 / (our chances multiplied), treating legs as independent. Bookies price SGMs below the
straight multi (TABtouch: 2 legs at 1.54 x 1.30 = 2.00 multi, SGM 1.82), so we estimate the SGM price
with SGM_CUT and only keep combos where that estimate still beats fair by MIN_EDGE.
"""
from itertools import combinations

MIN_PROB, MIN_LEGS, MAX_LEGS = 0.55, 2, 3
MULTI_MIN, MULTI_MAX = 1.70, 2.30
LEG_MIN_PRICE = 1.15
PER_GAME, LIMIT = 3, 12
SGM_CUT = {2: 0.91, 3: 0.86}   # bookie SGM price / legs multiplied (typical)
MIN_EDGE = 2.0                 # % value needed at the estimated SGM price
SCORING = {"Points", "Pts + Reb + Ast", "Pts + Reb", "Pts + Ast"}


def _ok(combo):
    players, teams, scoring = set(), {}, set()
    for l in combo:
        if l["player"] in players:
            return False
        players.add(l["player"])
        t = l.get("team") or l["player"]
        teams.setdefault(t, []).append(l["stat"])
        if l["stat"] in SCORING:
            if t in scoring:
                return False
            scoring.add(t)
    for stats in teams.values():
        if len(stats) > 2 or len(set(stats)) < len(stats):
            return False
    return True


def build(legs):
    by_game = {}
    for l in legs:
        if l["prob"] >= MIN_PROB:
            by_game.setdefault(l["ev"], []).append(l)
    out = []
    for ev, ls in by_game.items():
        books = {b for l in ls for b in l["prices"]}
        found = {}
        for book in books:
            pool = []
            for l in ls:
                pr = l["prices"].get(book)
                if pr and pr >= LEG_MIN_PRICE and l["prob"] * pr >= 1:
                    pool.append({**l, "bp": pr})
            # keep the best line per player + stat (the one with the most value)
            best = {}
            for l in pool:
                k = (l["player"], l["stat"])
                if k not in best or l["prob"] * l["bp"] > best[k]["prob"] * best[k]["bp"]:
                    best[k] = l
            pool = sorted(best.values(), key=lambda l: -l["prob"] * l["bp"])[:14]
            for n in range(MIN_LEGS, MAX_LEGS + 1):
                for c in combinations(pool, n):
                    multi, prob = 1.0, 1.0
                    for l in c:
                        multi *= l["bp"]
                        prob *= l["prob"]
                    if not (MULTI_MIN <= multi <= MULTI_MAX) or not _ok(c):
                        continue
                    est = multi * SGM_CUT[n]
                    if (prob * est - 1) * 100 < MIN_EDGE:
                        continue
                    sig = frozenset((l["player"], l["stat"]) for l in c)
                    item = {
                        "game": c[0]["game"], "start": c[0]["start"], "book": book,
                        "multi": round(multi, 2), "est": round(est, 2), "fair": round(1 / prob, 2), "prob": round(prob, 3),
                        "value": round((prob * est - 1) * 100, 1),
                        "legs": [{k: l[k] for k in ("player", "team", "stat", "line", "point", "prob", "l10", "l5", "avg")}
                                 | {"price": l["bp"]} for l in c],
                    }
                    # same legs at several bookies: keep the bookie that pays the most
                    if sig not in found or item["est"] > found[sig]["est"]:
                        found[sig] = item
        game = sorted(found.values(), key=lambda x: -x["value"])
        picked, used = [], set()
        for g in game:   # spread them out: don't show 3 near-identical combos
            ps = {l["player"] for l in g["legs"]}
            if len(ps & used) >= len(ps) - 1 and picked:
                continue
            picked.append(g)
            used |= ps
            if len(picked) >= PER_GAME:
                break
        out += picked
    out.sort(key=lambda x: -x["value"])
    return out[:LIMIT]
