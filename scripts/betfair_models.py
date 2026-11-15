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


# ---------------- racing: Betfair Hub ratings (thoroughbreds "kash", greyhounds "iggy-joey") ----------------
RACE_WIDGETS = {"Horse": "kash-ratings-model", "Greyhound": "iggy-joey"}
RACE_URL = "https://betfair-data-supplier-prod.herokuapp.com/api/widgets/{}/datasets?date={}&presenter=RatingsPresenter&json=true"
VALUE_MIN, VALUE_MAX_RATED = 10.0, 21.0   # % over the rated price to call it value; skip rated roughies


def racing_update(prev, now):
    """Rated price for every runner at today's / tomorrow's rated meetings, keyed by Betfair market id."""
    prev = prev or {}
    try:
        if prev.get("updated") and now - datetime.fromisoformat(prev["updated"]) < EVERY:
            return prev, "racing ratings: cached"
    except ValueError:
        pass
    races, calls, errs = {}, 0, 0
    day = now.astimezone(SYD).date()
    for kind, w in RACE_WIDGETS.items():
        for d in (day, day + timedelta(days=1)):
            try:
                calls += 1
                j = _get(RACE_URL.format(w, d.isoformat()))
            except Exception:
                errs += 1
                continue
            for m in (j or {}).get("meetings") or []:
                for r in m.get("races") or []:
                    mid = r.get("bfExchangeMarketId")
                    if not mid:
                        continue
                    rs = []
                    for x in r.get("runners") or []:
                        rp = _n(x.get("ratedPrice"))
                        nm = str(x.get("name") or "")
                        no = x.get("number") or (int(nm.split(".")[0]) if nm.split(".")[0].isdigit() else None)
                        if rp and rp > 1:
                            rs.append({"id": x.get("bfExchangeSelectionId"), "name": nm.split(". ", 1)[-1], "no": no, "rated": round(rp, 2)})
                    if rs:
                        rs.sort(key=lambda x: x["rated"])
                        races[mid] = {"id": mid, "kind": kind, "venue": str(m.get("name") or "").title(), "num": r.get("number"),
                                      "name": r.get("name") or "", "date": d.isoformat(), "runners": rs}
    if not races and prev.get("races") and errs:
        return prev, f"racing ratings: kept previous ({errs} errors)"
    return ({"updated": now.isoformat(), "races": races},
            f"racing ratings: {len(races)} races from {calls} calls" + (f", {errs} errors" if errs else ""))


def racing_value(ratings, racing, now, fresh_min=20, window_min=90):
    """Runners whose Betfair back price beats the Hub's rated price, in races jumping soon. Needs a fresh
    exchange pull (racing upload); returns [] without one."""
    out = []
    if not ratings or not racing or not racing.get("races"):
        return out
    try:
        age = (now - datetime.fromisoformat(str(racing.get("updated")).replace("Z", "+00:00"))).total_seconds() / 60
    except ValueError:
        return out
    if age > fresh_min:
        return out
    R = ratings.get("races") or {}
    for race in racing["races"]:
        rr = R.get(race["id"])
        if not rr:
            continue
        try:
            mins = (datetime.fromisoformat(str(race["start"]).replace("Z", "+00:00")) - now).total_seconds() / 60
        except ValueError:
            continue
        if not (0 < mins <= window_min):
            continue
        rated = {x["id"]: x for x in rr["runners"]}
        for x in race.get("runners") or []:
            m = rated.get(x["id"])
            if not m or not x.get("back") or m["rated"] > VALUE_MAX_RATED:
                continue
            edge = (x["back"] / m["rated"] - 1) * 100
            if edge < VALUE_MIN:
                continue
            fav = rr["runners"][0]
            out.append({"sport": rr["kind"] == "Horse" and "Racing" or "Greyhounds", "sport_key": "racing_" + rr["kind"].lower(),
                        "game": f"{race['venue']} {race['name'].split(' ')[0]}", "start": race["start"], "market": "Win",
                        "pick": f"{(str(x.get('no')) + '. ') if x.get('no') else ''}{m['name']}", "price": x["back"], "book": "Betfair",
                        "fair": m["rated"], "edge_pct": round(edge, 1), "fair_src": "betfair-model",
                        "why": (f"Betfair's {'thoroughbred' if rr['kind'] == 'Horse' else 'greyhound'} model rates {m['name']} at "
                                f"${m['rated']:.2f}; the exchange is offering ${x['back']:.2f} to back ({edge:.0f}% over). "
                                + (f"The model's top pick is {fav['name']} (${fav['rated']:.2f})." if fav["id"] != x["id"] else "It's the model's top pick in the race.")
                                + " Check your bookie's price is at least the rated price before betting.")})
    out.sort(key=lambda o: -o["edge_pct"])
    return out[:10]


def racing_tops(ratings, now, horses=8, dogs=4):
    """Today's standout model picks: races where the top-rated runner is clearly ahead of the next best
    (short rated price, big gap to the second). For the morning racing push."""
    day = now.astimezone(SYD).date().isoformat()
    picks = []
    for r in ((ratings or {}).get("races") or {}).values():
        rs = r.get("runners") or []
        if r.get("date") != day or len(rs) < 3:
            continue
        a, b = rs[0], rs[1]
        if a["rated"] > 3.5:
            continue
        picks.append({"kind": r["kind"], "venue": r["venue"], "num": r.get("num"), "no": a.get("no"), "name": a["name"],
                      "rated": a["rated"], "next": b["name"], "next_rated": b["rated"], "gap": round(b["rated"] / a["rated"], 2)})
    picks.sort(key=lambda x: -x["gap"])
    out = [p for p in picks if p["kind"] == "Horse"][:horses] + [p for p in picks if p["kind"] == "Greyhound"][:dogs]
    return {"date": day, "items": out}
