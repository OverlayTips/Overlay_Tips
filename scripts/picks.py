"""Daily 'best picks' summary from the odds snapshot.

Overlays: a bookie's price beats the no-vig consensus of all the other bookies by EDGE_MIN or more.
Arbs:     best prices across bookies add up to under 100%.
Steamers: best price has shortened a lot since the tracker first saw it.

These are price signals, not guaranteed winners.
"""
import base64, gzip, json, os, secrets
from datetime import datetime, timedelta, timezone
from statistics import median

EDGE_MIN = 0.03        # 3% above the Pinnacle (sharp) fair price
EDGE_MIN_MKT = float(os.environ.get("EDGE_MIN_MKT", "5")) / 100   # no Pinnacle: soft-bookie consensus is noisier, so need more
# Only quote prices at bookies we can actually bet with (everyone else still counts toward the fair price).
MY_BOOKS = {b.strip() for b in os.environ.get("MY_BOOKS", "sportsbet,tab,tabtouch,betr_au").split(",") if b.strip()}
EDGE_MAX = 0.15        # above this it's nearly always a stale or wrong price, not real value: left out
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
    # two-sided markets: pick ONE line for the pair, so Over/Under (or Team -x / Team +x) always
    # sit on the same number. Choosing each side separately could give Under 7.5 vs Over 8.5,
    # which made fake arbs and bad no-vig prices.
    for (mk, d), nm in names.items():
        if len(nm) != 2:
            continue
        a, b = sorted(nm)
        pa, pb = per.get((mk, d, a), {}), per.get((mk, d, b), {})
        flip = (lambda p: -p) if mk.endswith("spreads") else (lambda p: p)

        def pair_score(p):
            ba, bb = pa.get(p, {}), pb.get(flip(p), {})
            near = sum(-abs(sorted(x.values())[len(x) // 2] - 1.91) for x in (ba, bb) if x)
            return (len(set(ba) & set(bb)), len(ba) + len(bb), near, -abs(p))

        cands = sorted(set(pa) | {flip(p) for p in pb})
        p = max(cands, key=pair_score) if cands else None
        if p is not None and pair_score(p)[0] > 0:
            main[(mk, d, a)], main[(mk, d, b)] = p, flip(p)
    for (mk, d, name), pts in per.items():
        if (mk, d, name) in main:
            continue
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


def _same_line(mk, main, sides):
    """True if every side of the group is priced at the same line (spreads: mirrored)."""
    pts = [main.get(s) for s in sides]
    if all(p is None for p in pts):
        return True            # h2h etc.
    if any(p is None for p in pts) or len(pts) != 2:
        return False
    return pts[0] == (-pts[1] if mk.endswith("spreads") else pts[1])


def _game(ev):
    return f"{ev['home_team']} v {ev['away_team']}"


def sharp_fair(ev, mk, desc, sides, main):
    """No-vig probabilities from the sharp book (Pinnacle) at the same line as the main line, or {}."""
    sb = ev.get("sharp")
    if not sb:
        return {}
    m = next((x for x in sb.get("markets", []) if x["key"] == mk), None)
    if not m:
        return {}
    pr = {}
    for s in sides:
        o = next((o for o in m["outcomes"] if o["name"] == s and (o.get("description") or "") == desc
                  and o.get("point") == main.get(s)), None)
        if not o:
            return {}
        pr[s] = 1 / o["price"]
    tot = sum(pr.values())
    return {s: v / tot for s, v in pr.items()}


BF_COMM = float(os.environ.get("BF_COMMISSION", "6")) / 100   # Betfair commission on winnings (set yours)


def lay_arbs(pool, c=None):
    """Back at a bookie, lay the same selection on Betfair: back S at B, lay S*B/(L-c) at L.
    Every result returns S*(B*(1-c)/(L-c) - 1). Keeps the best bookie price per selection, profit > 0."""
    c = BF_COMM if c is None else c
    out = []
    for ev in pool:
        bf = next((b for b in ev.get("bookmakers", []) if b["key"] == "betfair_ex_au"), None)
        if not bf:
            continue
        lays = {m["key"][:-4]: m["outcomes"] for m in bf.get("markets", []) if m["key"].endswith("_lay")}
        best = {}
        for b in ev["bookmakers"]:
            if b["key"] == "betfair_ex_au" or (MY_BOOKS and b["key"] not in MY_BOOKS):
                continue
            for m in b.get("markets", []):
                lo = lays.get(m["key"])
                if not lo:
                    continue
                for o in m.get("outcomes", []):
                    if not (o.get("price") or 0) > 1:
                        continue
                    l = next((x for x in lo if x.get("name") == o.get("name") and (x.get("description") or "") == (o.get("description") or "")
                              and (o.get("point") is None or x.get("point") == o.get("point"))), None)
                    if not l or not (l.get("price") or 0) > 1 + c:
                        continue
                    k = (m["key"], o.get("name"), o.get("description") or "", o.get("point"))
                    margin = o["price"] * (1 - c) / (l["price"] - c) - 1
                    if k not in best or margin > best[k]["margin_pct"] / 100:
                        who = f"{o['description']} " if o.get("description") else ""
                        pt = o.get("point")
                        sign = "+" if (m["key"] == "spreads" and pt is not None and pt > 0) else ""
                        best[k] = {"sport": ev.get("sport_title"), "sport_key": ev.get("sport_key"), "game": _game(ev),
                                   "start": ev["commence_time"], "market": _mlabel(m["key"]),
                                   "pick": f"{who}{o['name']}{(' ' + sign + str(pt)) if pt is not None else ''}",
                                   "back": o["price"], "book": SHORT.get(b["key"], b.get("title", b["key"])), "lay": l["price"],
                                   "comm_pct": round(c * 100, 1), "margin_pct": round(margin * 100, 2)}
        out += [v for v in best.values() if v["margin_pct"] > 0]
    return sorted(out, key=lambda x: -x["margin_pct"])


def compute(events, props, hist, now):
    overlays, arbs, steamers = [], [], []
    pool = [e for e in list(events) + list(props) if now < _parse(e["commence_time"]) <= now + WINDOW]
    seen_steam = set()   # game lines and props share an event id, so the same history would be read twice
    for ev in pool:
        for mk, desc, sides, main in _groups(ev):
            if len(sides) < 2:
                continue   # yes-only markets (anytime scorer etc.) can't be de-vigged
            if not _same_line(mk, main, sides):
                continue   # sides on different lines can't be de-vigged or arbed against each other
            # books that price every side of this group -> no-vig probabilities per book
            books = set.intersection(*(set(b) for b in sides.values()))
            fair = sharp_fair(ev, mk, desc, sides, main)
            src = "pin" if fair else "mkt"
            if not fair and len(books) >= MIN_BOOKS:
                per = {s: [] for s in sides}
                for b in books:
                    inv = {s: 1 / sides[s][b][0] for s in sides}
                    tot = sum(inv.values())
                    for s in sides:
                        per[s].append(inv[s] / tot)
                fair = {s: median(v) for s, v in per.items()}
            best = {}
            for s, bk in sides.items():
                cands = [(p, b) for b, (p, _) in bk.items() if b not in NO_BEST and (not MY_BOOKS or b in MY_BOOKS)]
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
                if (EDGE_MIN if src == "pin" else EDGE_MIN_MKT) <= edge <= EDGE_MAX:
                    overlays.append({"sport": ev.get("sport_title"), "sport_key": ev.get("sport_key"), "game": _game(ev), "start": ev["commence_time"],
                                     "market": _mlabel(mk), "pick": sel(s), "price": price,
                                     "book": SHORT.get(book, book), "fair": round(1 / fp, 2),
                                     "edge_pct": round(edge * 100, 1), "books": len(books), "fair_src": src,
                                     # raw keys so the bet log can find this exact selection again (closing price, result)
                                     "ev": ev["id"], "mk": mk, "desc": desc, "side": s, "point": main.get(s)})
            if len(best) == len(sides) and (mk != "h2h" or len(sides) >= 2):
                pct = sum(1 / p for p, _ in best.values())
                if pct < 1:
                    arbs.append({"sport": ev.get("sport_title"), "sport_key": ev.get("sport_key"), "game": _game(ev), "start": ev["commence_time"],
                                 "market": _mlabel(mk), "margin_pct": round((1 - pct) * 100, 2),
                                 "legs": [{"pick": sel(s), "price": p, "book": SHORT.get(b, b)} for s, (p, b) in best.items()]})
        for key, ser in (hist.get(ev["id"], {}).get("series") or {}).items():
            if len(ser) < 2 or key.split("|")[0].endswith("_lay") or (ev["id"], key) in seen_steam:
                continue
            seen_steam.add((ev["id"], key))
            a, z = ser[0], ser[-1]
            if key.endswith("|main") and (len(a) < 4 or len(z) < 4 or a[3] != z[3]):
                continue   # the main line moved (e.g. Over 1.5 -> Over 0.5): price % isn't a like-for-like move
            if key.endswith("|main") and f"{key[:-5]}|{z[3]}" in hist[ev["id"]]["series"]:
                continue   # same move is already covered by that line's own series
            pct = (z[1] - a[1]) / a[1] * 100
            if pct <= -8:
                mk, *rest = key.split("|")
                steamers.append({"sport": ev.get("sport_title"), "game": _game(ev), "start": ev["commence_time"],
                                 "market": _mlabel(mk), "pick": " ".join(x for x in rest if x != "None"), "from": a[1], "to": z[1],
                                 "pct": round(pct, 1)})
    overlays.sort(key=lambda x: -x["edge_pct"])
    arbs.sort(key=lambda x: -x["margin_pct"])
    steamers.sort(key=lambda x: x["pct"])
    layarbs = lay_arbs(pool)
    return {"overlays": overlays[:15], "arbs": arbs[:10], "layarbs": layarbs[:10], "steamers": steamers[:10],
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
