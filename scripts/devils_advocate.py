"""Devil's advocate: argues AGAINST each value alert, so every pick has to survive a second look.

For every alert value_alerts.py sends, case(p, record) goes through the same numbers the model used and pulls out
everything that cuts against the bet: the market disagreeing with us, a lone outlier price, a thin cushion over the
line, form or minutes trending down, a bad matchup, an injury tag, a small sample, a guessed fair price, and how this
league + market type has actually gone in the bet log. Each point carries a weight; the total is the "case against".

Flag only: it never blocks an alert. It's posted as a thread under the alert in Discord (discord_post.alert), and its
score is saved on the bet log row ("da"), so bet_log.py can show how picks went by how strong the case against was.
If strong-case picks keep losing and weak-case picks keep winning, it's earned a veto; if not, ignore it.

Optional env: DEVIL_BOT_TOKEN, a second Discord bot's token, so the thread posts under its own name/avatar.
"""
import math

STRONG, SOME = 4, 2   # score -> "strong" (4+), "some" (2-3), "weak" (0-1)


def level(score):
    return "strong" if score >= STRONG else "some" if score >= SOME else "weak"


def _rate(x):
    return x[0] / x[1] if x and x[1] else None


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def _prop(v, side, pts):
    """Points against a player prop, from its Hot stats item."""
    over = side == "Over"
    pt, avg, stat = v.get("point"), v.get("avg"), str(v.get("stat") or "").lower()
    games = v.get("games") or []
    vals = [g[3] for g in games if g[3] is not None]

    # 1. the market vs us: most of our edge is form the bookies can see too
    prob, mkt = v.get("prob"), v.get("mkt")
    if prob and mkt and prob - mkt >= 0.08:
        sharp = v.get("msrc") == "pin"
        pts.append((2 if sharp else 1, f"{'Pinnacle' if sharp else 'The market'} has this at {100 * mkt:.0f}%, we've got "
                                       f"{100 * prob:.0f}%. That gap is mostly his form, and the bookies see the same box scores"))
    if v.get("msrc") == "one":
        pts.append((1, "No Unders priced on this one, so our 'fair' price is a guess off the Over ladder, not a real two-way market"))

    # 2. one bookie way out on its own = often a stale line about to move, or one that gets limited
    prices = list((v.get("prices") or {}).values())
    med = _median(prices)
    if med and v.get("price") and len(prices) >= 3:
        gap = v["price"] / med - 1
        if gap >= 0.08:
            pts.append((2 if gap >= 0.12 else 1, f"Only {v.get('book')} is at ${v['price']:.2f}, most books are ${med:.2f}. "
                                                  f"Lone outliers usually get pulled into line fast"))
        elif len(prices) <= 3:
            pts.append((1, f"Only {len(prices)} bookies even have this line, thin market"))

    # 3. thin cushion: the average barely clears the line, and a few clears were by a hair
    if pt is not None and avg is not None and pt > 0:
        cushion = (avg - pt) / max(pt, 1) if over else (pt - avg) / max(pt, 1)
        if cushion < 0.05:
            pts.append((2 if cushion < 0 else 1, f"He's averaging {avg} on a {pt} line, barely any cushion"
                                                  if cushion >= 0 else f"He's averaging {avg}, that's the wrong side of {pt}"))
        close = [x for x in vals if abs(x - pt) <= max(0.5, 0.08 * pt)]   # cleared or missed it by one (or ~8% on big lines)
        if len(vals) >= 6 and len(close) >= 0.6 * len(vals):
            pts.append((1, f"{len(close)} of his last {len(vals)} landed within a whisker of the line, coin-flip territory"))

    # 4. trending the wrong way
    l5, l10 = _rate(v.get("l5")), _rate(v.get("l10"))
    if l5 is not None and l10 is not None and l5 <= l10 - 0.2:
        pts.append((1, f"Cooling off: {v['l5'][0]}/{v['l5'][1]} last 5 vs {v['l10'][0]}/{v['l10'][1]} last 15"))
    if len(vals) >= 6 and pt is not None:
        last3 = vals[:3]
        if (over and all(x <= pt for x in last3)) or (not over and all(x >= pt for x in last3)):
            pts.append((2, f"Missed it the last 3 straight ({', '.join(str(x) for x in last3)})"))

    # 5. workload (minutes for basketball; skip NFL, its logs have no minutes)
    mins = [g[4] for g in games if len(g) > 4 and g[4]]
    if len(mins) >= 6:
        r3, r10 = sum(mins[:3]) / 3, sum(mins) / len(mins)
        if r3 < r10 * 0.9:
            pts.append((1, f"Minutes are dropping: {r3:.0f} a game over his last 3 vs {r10:.0f} over 10"))
    proj, mean = v.get("proj") or {}, v.get("mean")
    if mean is not None and pt is not None and ((over and mean < pt) or (not over and mean > pt)):
        pts.append((2, f"Our own minutes x rate projection has him at {mean} for tonight, {'under' if over else 'over'} the {pt}"))
    elif proj.get("min") and mins and proj["min"] < sum(mins) / len(mins) * 0.9:
        pts.append((1, f"Projected for ~{proj['min']:.0f} minutes, less than he's been playing"))

    # 6. matchup (pct = what the opponent allows his position for this stat vs league average)
    nfl = v.get("nfl") or {}
    mu = v.get("mu") or nfl.get("def") or {}
    if mu.get("pct") is not None and ((over and mu["pct"] <= -8) or (not over and mu["pct"] >= 8)):
        pts.append((1, f"{v.get('opp') or 'The opponent'} {'gives up less' if over else 'gives up more'} than most to his position "
                       f"for {stat} ({mu['pct']:+.0f}% vs league avg)"))
    vs = v.get("vs")
    if vs and vs[1] >= 2 and _rate(vs) < 0.5:
        pts.append((1, f"Only {vs[0]}/{vs[1]} against {v.get('opp')} this season"))
    use = nfl.get("use") or {}
    if use.get("season") and use.get("l3") is not None and use["l3"] < use["season"] * 0.85 and over:
        pts.append((1, f"Workload's down: {use['l3']} {use.get('what', '')} a game over his last 3 vs {use['season']} on the season"))
    gm = nfl.get("gm") or {}
    if gm.get("tt") is not None and gm["tt"] < 19 and over:
        pts.append((1, f"His team's only implied for ~{gm['tt']:.0f} points tonight, not a lot of offence to go round"))

    # 7. availability and sample size
    if v.get("inj") in ("Q", "GTD"):
        pts.append((2, f"He's listed {v['inj']}. If he plays hurt or on a limit, the form means nothing"))
    elif v.get("inj") == "Bench":
        pts.append((2, "Coming off the bench tonight, a smaller role than the games behind this"))
    season = v.get("season") or [0, 0]
    if season[1] and season[1] < 8:
        pts.append((1, f"Only {season[1]} games of data this season, small sample"))
    wo = v.get("wo")
    if wo and wo.get("g", 99) < 5:
        pts.append((1, f"The with/without bump is built on just {wo['g']} games without {wo['n'].split()[-1]}"))
    if v.get("moved"):
        pts.append((1, "He's changed teams this season, older games came in a different role"))


def _game(o, pts):
    """Points against a game-market overlay (picks.json)."""
    src = str(o.get("fair_src") or "")
    if src not in ("pin", "betfair"):
        pts.append((1, "Fair price is the Aussie bookies' consensus, not a sharp book. Consensus can be wrong together"))
    if (o.get("edge_pct") or 0) >= 10:
        pts.append((2, f"A {o['edge_pct']:.0f}% edge on a main market is huge. Usually that's a stale price or news we haven't seen"))
    if not o.get("model"):
        pts.append((1, "Betfair's model has no rating on this one, so nothing independent backs it"))
    f = o.get("form")
    if f and f[1] and f[0] / f[1] < 0.7:
        pts.append((1, f"Form only backs it {f[0]}/{f[1]}"))


def _record(p, record, pts, bucket_name):
    """How this league + market type has actually gone in the bet log."""
    for g in (record or {}).get("groups") or []:
        if g.get("name") != bucket_name:
            continue
        if (g.get("clv_n") or 0) >= 20 and (g.get("clv") or 0) < 0:
            pts.append((2, f"{bucket_name}s have lost to the closing price on average ({g['clv']:+.1f}% CLV over {g['clv_n']})"))
        elif (g.get("w") or 0) + (g.get("l") or 0) >= 20 and (g.get("roi") or 0) < -10:
            pts.append((1, f"{bucket_name}s are {g['w']}-{g['l']} ({g['roi']:+.0f}% ROI) so far"))
        elif (g.get("n") or 0) < 15:
            pts.append((1, f"We've only sent {g.get('n') or 0} {bucket_name}s so far, the model's untested here"))
        return


def case(p, record=None, bucket_name=None):
    """{"score", "level", "points": [text, ...]} against one alert dict from value_alerts.collect()."""
    pts = []
    src = p.get("src_item") or {}
    side = (p.get("log") or {}).get("side") or "Over"
    try:
        if p.get("key", "").startswith("hot|"):
            _prop(src, side, pts)
        elif p.get("key", "").startswith("ov|"):
            _game(src, pts)
        if bucket_name:
            _record(p, record, pts, bucket_name)
    except Exception as e:   # never stop an alert over this
        print(f"::warning::devil's advocate: {type(e).__name__} {e}")
    pts.sort(key=lambda x: -x[0])
    score = sum(w for w, _ in pts)
    return {"score": score, "level": level(score), "points": [t for _, t in pts]}


def thread(d, sel):
    """(thread name, message lines) for the Discord thread."""
    head = {"strong": "💪 Strong case against", "some": "🤏 Some case against", "weak": "🙂 Not much against it"}[d["level"]]
    lines = [f"**🧐 Devil's advocate · {head}** ({d['score']})"]
    if d["points"]:
        lines += [f"• {t}" for t in d["points"][:7]]
    else:
        lines.append("Went through the numbers and couldn't find much against this one.")
    lines.append("-# Flag only, the pick still stands. Tracking whether the case against is worth listening to.")
    name = f"🧐 Devil's advocate · {sel}"
    return name[:95], lines
