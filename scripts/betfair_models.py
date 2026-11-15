"""Betfair Hub prediction models (Betfair's data scientists) as a second opinion on game markets.

Betfair publishes a rated price and a predicted score for every NBA game (and NFL / AFL when in season) on
the Betfair Hub. We use them only as a check on our own value bets, credited to Betfair, never re-published
as our own ratings:
  - "agree": the model's price for the pick is shorter than the price we're getting (it also sees value)
  - head-to-head uses the rated price; line and total use the model's predicted margin / total
Pulled at most every 30 minutes; any failure keeps the previous pull.

update(prev, events, now) -> ({"updated", "games": {"<away>@<home>": {"lg", "start", "teams": {name: {"price", "score"}}}}}, note)
check(o, model) -> None | {"price"?, "score": "A-B", "agree": bool, "why": str}   for an overlay from picks.py
"""
import json, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = "https://betfair-data-supplier-prod.herokuapp.com/api/widgets/{}/datasets?date={}&json=true"
WIDGETS = {"basketball_nba": "nba-model", "americanfootball_nfl": "nfl-model", "aussierules_afl": "afl-model"}
EVERY = timedelta(minutes=30)
SYD = ZoneInfo("Australia/Sydney")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "overlay-tips/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _n(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def update(prev, events, now):
    prev = prev or {}
    try:
        if prev.get("updated") and now - datetime.fromisoformat(prev["updated"]) < EVERY:
            return prev, "betfair models: cached"
    except ValueError:
        pass
    sports = {e.get("sport_key") for e in events} & set(WIDGETS)
    games, calls, errs = {}, 0, 0
    day = now.astimezone(SYD).date()
    for sk in sports:
        for d in (day - timedelta(days=1), day, day + timedelta(days=1), day + timedelta(days=2)):
            try:
                calls += 1
                j = _get(BASE.format(WIDGETS[sk], d.isoformat()))
            except Exception:
                errs += 1
                continue
            for comp in (j or {}).get("competitions") or []:
                for m in comp.get("matches") or []:
                    name = m.get("name") or ""
                    if " @ " not in name:
                        continue
                    away, home = [x.strip() for x in name.split(" @ ", 1)]
                    for mk in m.get("markets") or []:
                        rs = mk.get("runners") or []
                        if len(rs) < 2:
                            continue
                        games[f"{away}@{home}"] = {"lg": comp.get("name") or sk, "sk": sk,
                                                   "teams": {r.get("name"): {"price": _n(r.get("price")), "score": _n(r.get("score"))} for r in rs}}
    if not games and prev.get("games") and errs:
        return prev, f"betfair models: kept previous ({errs} errors)"
    return ({"updated": now.isoformat(), "games": games},
            f"betfair models: {len(games)} games from {calls} calls" + (f", {errs} errors" if errs else ""))


def _game(o, model):
    g = str(o.get("game") or "")
    for sep in (" @ ", " v ", " vs "):
        if sep in g:
            a, b = [x.strip() for x in g.split(sep, 1)]
            for k in (f"{a}@{b}", f"{b}@{a}"):
                if k in (model or {}).get("games", {}):
                    return model["games"][k]
    return None


def check(o, model):
    """Does Betfair's model back this overlay? Only for head-to-head, line and total on games it rates."""
    g = _game(o, model)
    mk, side, pt, price = o.get("mk"), o.get("side"), o.get("point"), o.get("price")
    if not g or mk not in ("h2h", "spreads", "totals"):
        return None
    T = g["teams"]
    names = list(T)
    sc = {n: T[n].get("score") for n in names}
    score = " – ".join(f"{n.split()[-1]} {sc[n]:g}" for n in names if sc[n] is not None)
    if mk == "h2h":
        t = T.get(side)
        if not t or not t.get("price"):
            return None
        agree = t["price"] <= price
        return {"price": t["price"], "score": score, "agree": agree,
                "why": f"Betfair's model rates {side} at ${t['price']:.2f}" + (f" (predicted {score})" if score else "")
                       + (", so it backs the price." if agree else ", so it doesn't see value at this price.")}
    if any(v is None for v in sc.values()) or not isinstance(pt, (int, float)):
        return None
    if mk == "spreads":
        me = side if side in sc else None
        if not me:
            return None
        other = next(n for n in names if n != me)
        margin = sc[me] - sc[other]             # predicted winning margin for the pick
        agree = margin + pt > 0                 # e.g. pick -5.5 needs margin > 5.5
        return {"score": score, "agree": agree,
                "why": f"Betfair's model has it {score} (a {abs(margin):.1f}-point {'win' if margin >= 0 else 'loss'} for {me.split()[-1]}), "
                       + ("which covers the line." if agree else "which doesn't cover the line.")}
    tot = sum(sc.values())
    agree = tot > pt if side == "Over" else tot < pt if side == "Under" else None
    if agree is None:
        return None
    return {"score": score, "agree": agree,
            "why": f"Betfair's model predicts {score} ({tot:g} total), " + ("on the right side of the line." if agree else "on the wrong side of the line.")}
