"""Daily 'best picks' summary from the odds snapshot.

Overlays: a bookie's price beats the no-vig consensus of all the other bookies by EDGE_MIN or more.
Arbs:     best prices across bookies add up to under 100%.
Steamers: best price has shortened a lot since the tracker first saw it.

These are price signals, not guaranteed winners.
"""
import base64, gzip, json, os, secrets
from datetime import datetime, timedelta, timezone
from statistics import median

EDGE_MIN = 0.03        # 3% above consensus fair price
MIN_BOOKS = 4          # need at least this many bookies pricing a market to trust the consensus
WINDOW = timedelta(hours=26)
NO_BEST = {"betfair_ex_au"}   # exchange price is pre-commission: use it for consensus, not as the pick
SHORT = {"sportsbet": "Sportsbet", "tab": "TAB", "tabtouch": "TABtouch", "neds": "Neds", "ladbrokes_au": "Ladbrokes",
         "pointsbetau": "PointsBet", "unibet": "Unibet", "betfair_ex_au": "Betfair", "betr_au": "Betr",
         "betright": "BetRight", "playup": "PlayUp", "bet365_au": "bet365", "dabble_au": "Dabble",
         "picklebet_au": "Picklebet"}
MKT = {"h2h": "H2H", "spreads": "Line", "totals": "Total"}


def _parse(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _mlabel(k):
    return MKT.get(k) or k.replace("player_", "").replace("_", " ").title()


def _groups(ev):
    """Yield (market_key, group_name, {side_label: {book: (price, point)}}, main) at each side's main line."""
    main = main_points(ev)
    raw = {}
    for bk in ev.get("bookmakers", []):
        for m in bk.get("markets", []):
            if m["key"].endswith("_lay"):
                continue   # Betfair lay prices aren't backable, so they'd create fake arbs
            for o in m.get("outcomes", []):
                d = o.get("description") or ""
                mp = main.get((m["key"], d, o["name"]))
                if mp is not None and o.get("point") != mp:
                    continue
                raw.setdefault((m["key"], d), {}).setdefault(o["name"], {})[bk["key"]] = (o["price"], o.get("point"))
    for (mk, desc), sides in raw.items():
        m = {side: main[(mk, desc, side)] for side in sides if (mk, desc, side) in main}
        yield mk, desc, sides, m

def _opp_names(names, name):
    return [n for n in names if n != name]


def main_points(ev):
    """Pick the 'main' line for every (market, player, side).

    Bookies (esp. Aussie ones) often send ladders/milestones (Over 14.5, 19.5, 24.5 ...) in the same
    market. The main line is the one most bookies price on BOTH sides (Over and Under at the same line,
    or Team -x / Team +x), then the most bookies, then the price closest to evens."""
    per = {}      # (mk, desc, name) -> {point: {book: price}}
    names = {}    # (mk, desc) -> set(names)
    for bk in ev.get("bookmakers", []):
        for m in bk.get("markets", []):
            if m["key"].endswith("_lay"):
                continue
            for o in m.get("outcomes", []):
                if o.get("point") is None:
                    continue
                d = o.get("description") or ""
                names.setdefault((m["key"], d), set()).add(o["name"])
                per.setdefault((m["key"], d, o["name"]), {}).setdefault(o["point"], {})[bk["key"]] = o["price"]
    main = {}
    for (mk, d, name), pts in per.items():
        opps = _opp_names(names.get((mk, d), ()), name)
        def score(p):
            books = pts[p]
            paired = 0
            for on in opps:
                op = per.get((mk, d, on), {}).get(-p if mk == "spreads" else p, {})
                paired = max(paired, len(set(books) & set(op)))
            prices = sorted(books.values())
            med = prices[len(prices) // 2]
            return (paired, len(books), -abs(med - 1.91))
        main[(mk, d, name)] = max(pts, key=score)
    return main


def _game(ev):
    return f"{ev['home_team']} v {ev['away_team']}"


def compute(events, props, hist, now):
    overlays, arbs, steamers = [], [], []
    pool = [e for e in list(events) + list(props) if now < _parse(e["commence_time"]) <= now + WINDOW]
    for ev in pool:
        for mk, desc, sides, main in _groups(ev):
            if len(sides) < 2:
                continue   # yes-only markets (anytime scorer etc.) can't be de-vigged
            # books that price every side of this group -> no-vig probabilities per book
            books = set.intersection(*(set(b) for b in sides.values()))
            fair = {}
            if len(books) >= MIN_BOOKS:
                per = {s: [] for s in sides}
                for b in books:
                    inv = {s: 1 / sides[s][b][0] for s in sides}
                    tot = sum(inv.values())
                    for s in sides:
                        per[s].append(inv[s] / tot)
                fair = {s: median(v) for s, v in per.items()}
            best = {}
            for s, bk in sides.items():
                cands = [(p, b) for b, (p, _) in bk.items() if b not in NO_BEST] or [(p, b) for b, (p, _) in bk.items()]
                if cands:
                    best[s] = max(cands)
            def sel(s):
                pt = main.get(s)
                who = f"{desc} " if desc else ""
                sign = "+" if (mk == "spreads" and pt is not None and pt > 0) else ""
                return f"{who}{s}{(' ' + sign + str(pt)) if pt is not None else ''}"
            for s, fp in fair.items():
                if s not in best:
                    continue
                price, book = best[s]
                edge = price * fp - 1
                if edge >= EDGE_MIN:
                    overlays.append({"sport": ev.get("sport_title"), "game": _game(ev), "start": ev["commence_time"],
                                     "market": _mlabel(mk), "pick": sel(s), "price": price,
                                     "book": SHORT.get(book, book), "fair": round(1 / fp, 2),
                                     "edge_pct": round(edge * 100, 1), "books": len(books)})
            if len(best) == len(sides) and (mk != "h2h" or len(sides) >= 2):
                pct = sum(1 / p for p, _ in best.values())
                if pct < 1:
                    arbs.append({"sport": ev.get("sport_title"), "game": _game(ev), "start": ev["commence_time"],
                                 "market": _mlabel(mk), "margin_pct": round((1 - pct) * 100, 2),
                                 "legs": [{"pick": sel(s), "price": p, "book": SHORT.get(b, b)} for s, (p, b) in best.items()]})
        for key, ser in (hist.get(ev["id"], {}).get("series") or {}).items():
            if len(ser) < 2 or key.split("|")[0].endswith("_lay"):
                continue
            a, z = ser[0], ser[-1]
            pct = (z[1] - a[1]) / a[1] * 100
            if pct <= -8:
                mk, *rest = key.split("|")
                steamers.append({"sport": ev.get("sport_title"), "game": _game(ev), "start": ev["commence_time"],
                                 "market": _mlabel(mk), "pick": " ".join(rest), "from": a[1], "to": z[1],
                                 "pct": round(pct, 1)})
    overlays.sort(key=lambda x: -x["edge_pct"])
    arbs.sort(key=lambda x: -x["margin_pct"])
    steamers.sort(key=lambda x: x["pct"])
    return {"overlays": overlays[:15], "arbs": arbs[:10], "steamers": steamers[:10],
            "games_in_window": len(pool)}


def encrypt_with_key(obj, key_b64):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = base64.b64decode(key_b64)
    iv = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(iv, gzip.compress(json.dumps(obj).encode()), None)
    return {"v": 1, "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}


def decrypt_with_key(bundle, key_b64):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    pt = AESGCM(base64.b64decode(key_b64)).decrypt(base64.b64decode(bundle["iv"]), base64.b64decode(bundle["ct"]), None)
    return json.loads(gzip.decompress(pt))


if __name__ == "__main__":
    # Decrypt helper for the daily alert:  python scripts/picks.py picks.json  (key in ODDS_ALERTS_KEY)
    import sys
    with open(sys.argv[1]) as f:
        print(json.dumps(decrypt_with_key(json.load(f), os.environ["ODDS_ALERTS_KEY"]), indent=1))
