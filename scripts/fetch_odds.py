#!/usr/bin/env python3
"""Pull Aussie bookie odds (game markets + player props) from The Odds API,
keep a rolling price history, and publish it all as one password-encrypted file.

Output: OUT_DIR/data.json  ->  {"v":1,"iter":N,"salt":b64,"iv":b64,"ct":b64}
  ct = AES-256-GCM( gzip( JSON{odds, props, history} ) ), key = PBKDF2-SHA256(password, salt, N)
The odds page asks for the password and decrypts in the browser, so the public
repo only ever holds ciphertext.

Env:
  ODDS_API_KEY         required
  ODDS_PAGE_PASSWORD   required (the password for the odds page)
  SPORTS               game-market sports (default below); 'soccer_*' = every active soccer league
  MARKETS              default h2h,spreads,totals
  PROP_SPORTS          sports to pull player props for (default below)
  PROPS_EVERY_HOURS    re-pull props for a game at most this often (default 6)
  PROPS_WINDOW_HOURS   only pull props for games starting within this many hours (default 24)
  MIN_CREDITS          stop spending once remaining credits drop below this (default 50)
"""
import base64, gzip, hashlib, json, os, secrets, sys, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

API = os.environ.get("ODDS_API_BASE", "https://api.the-odds-api.com/v4")
KEY = os.environ.get("ODDS_API_KEY", "").strip()
PASSWORD = os.environ.get("ODDS_PAGE_PASSWORD", "")
SPORTS = [s.strip() for s in os.environ.get("SPORTS",
          "basketball_nba,basketball_nbl,aussierules_afl,rugbyleague_nrl,soccer_epl,americanfootball_nfl,soccer_*").split(",") if s.strip()]
MARKETS = os.environ.get("MARKETS", "h2h,spreads,totals")
PROP_SPORTS = [s.strip() for s in os.environ.get("PROP_SPORTS",
               "basketball_nba,basketball_nbl,aussierules_afl,rugbyleague_nrl,americanfootball_nfl,soccer_epl").split(",") if s.strip()]
_BBALL = ("player_points,player_rebounds,player_assists,player_threes,player_points_rebounds_assists,"
          "player_points_rebounds,player_points_assists,player_rebounds_assists,player_blocks,player_steals,"
          "player_turnovers,player_double_double,player_first_basket,"
          # ladders: every line a bookie offers (10+, 15+, 20+ points; 1+, 2+ assists ...)
          "player_points_alternate,player_rebounds_alternate,player_assists_alternate,player_threes_alternate")
# Only markets that actually come back are charged, so asking for extras costs nothing when a bookie doesn't offer them.
PROP_MARKETS = {
    "basketball_nba": _BBALL,
    "basketball_nbl": _BBALL,
    "aussierules_afl": ("player_disposals,player_goal_scorer_anytime,player_goal_scorer_first,player_goals_scored_over,"
                        "player_marks_over,player_tackles_over,player_afl_fantasy_points"),
    "rugbyleague_nrl": "player_try_scorer_anytime,player_try_scorer_first,player_try_scorer_over",
    "americanfootball_nfl": ("player_pass_yds,player_pass_tds,player_pass_completions,player_pass_attempts,"
                             "player_pass_interceptions,player_rush_yds,player_rush_attempts,player_receptions,"
                             "player_reception_yds,player_anytime_td,player_1st_td"),
    "soccer_epl": ("player_goal_scorer_anytime,player_first_goal_scorer,player_shots_on_target,player_shots,"
                   "player_assists,player_to_receive_card"),
}
PROPS_EVERY = timedelta(hours=float(os.environ.get("PROPS_EVERY_HOURS", "6")))
PROPS_WINDOW = timedelta(hours=float(os.environ.get("PROPS_WINDOW_HOURS", "24")))
SHARP_BOOK = os.environ.get("SHARP_BOOK", "pinnacle")
SHARP_SPORTS = [x.strip() for x in os.environ.get("SHARP_SPORTS", "basketball_nba").split(",") if x.strip()]
MIN_CREDITS = int(os.environ.get("MIN_CREDITS", "50"))
OUT = os.environ.get("OUT_DIR", "odds-data")
ITER = 250_000
KEEP_AFTER_START = timedelta(hours=6)   # drop games this long after tip-off

NOW = datetime.now(timezone.utc)
TS = NOW.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


# ---------------- crypto ----------------
def _key(salt):
    return hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), salt, ITER, 32)


def encrypt(obj):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = secrets.token_bytes(16), secrets.token_bytes(12)
    pt = gzip.compress(json.dumps(obj, separators=(",", ":")).encode())
    ct = AESGCM(_key(salt)).encrypt(iv, pt, None)
    b = lambda x: base64.b64encode(x).decode()
    return {"v": 1, "iter": ITER, "salt": b(salt), "iv": b(iv), "ct": b(ct)}


def decrypt(bundle):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    d = lambda k: base64.b64decode(bundle[k])
    salt = d("salt")
    key = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), salt, bundle.get("iter", ITER), 32)
    return json.loads(gzip.decompress(AESGCM(key).decrypt(d("iv"), d("ct"), None)))


def load_previous():
    try:
        with open(os.path.join(OUT, "data.json")) as f:
            return decrypt(json.load(f))
    except Exception as e:  # first run, or password changed
        print("No previous data loaded:", type(e).__name__)
        return {}


# ---------------- API ----------------
class Api:
    def __init__(self):
        self.remaining = None
        self.used = None
        self.errors = []

    def get(self, path, **params):
        params["apiKey"] = KEY
        url = f"{API}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "overlay-tips-odds/1.1"})
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.headers.get("x-requests-remaining") is not None:
                self.remaining = int(float(r.headers["x-requests-remaining"]))
                self.used = int(float(r.headers.get("x-requests-used", 0)))
            return json.load(r)

    def low(self):
        return self.remaining is not None and self.remaining < MIN_CREDITS

    def try_get(self, label, path, **params):
        if self.low():
            self.errors.append(f"skipped {label}: only {self.remaining} credits left")
            return None
        try:
            return self.get(path, **params)
        except urllib.error.HTTPError as e:
            self.errors.append(f"{label}: HTTP {e.code} {e.read()[:200].decode(errors='ignore')}")
        except Exception as e:
            self.errors.append(f"{label}: {type(e).__name__} {e}")
        return None


# ---------------- history ----------------
def okey(market, o):
    # one series per side; game lines keep one series through line moves (-4.5 -> -6.5 is a move),
    # player props get one series per line so prices are only ever compared at the same line
    who = o.get("description")
    if who:
        return f"{market}|{who}|{o['name']}|{o.get('point')}"
    return f"{market}|{o['name']}"


def record(hist, ev):
    import picks
    main = picks.main_points(ev)
    best = {}
    for bk in ev.get("bookmakers", []):
        for m in bk.get("markets", []):
            if m["key"].endswith("_lay") or m["key"].endswith("_alternate"):
                continue   # ladders (10+, 15+ ...) aren't tracked over time
            for o in m.get("outcomes", []):
                mp = main.get((m["key"], o.get("description") or "", o["name"]))
                if mp is not None and o.get("point") != mp:
                    continue
                k = okey(m["key"], o)
                if k not in best or o["price"] > best[k][0]:
                    best[k] = (o["price"], bk["key"], o.get("point"))
    he = hist.setdefault(ev["id"], {"commence_time": ev["commence_time"], "series": {}})
    he["commence_time"] = ev["commence_time"]
    items = list(best.items())
    # props: also keep one series per player+side that follows the main line, so line moves show up
    items += [(k.rsplit("|", 1)[0] + "|main", v) for k, v in best.items() if k.count("|") == 3]
    for k, (price, book, point) in items:
        ser = he["series"].setdefault(k, [])
        if not ser or ser[-1][1] != price or ser[-1][3] != point:
            ser.append([TS, price, book, point])
        ser[:] = ser[:1] + ser[1:][-47:]   # opener + last 47 changes


def record_bookies(hist, ev):
    """Player props: every bookie's price at the main line plus the fair price, one snapshot per change.
    hist[id]["px"]["market|player"] = [[TS, {side: [point, fair_price, {book: price}]}], ...]"""
    import picks
    he = hist.setdefault(ev["id"], {"commence_time": ev["commence_time"], "series": {}})
    px = he.setdefault("px", {})
    for mk, desc, sides, main in picks._groups(ev):
        if not desc or len(sides) < 2:
            continue
        books = set.intersection(*(set(b) for b in sides.values()))
        fair = picks.sharp_fair(ev, mk, desc, sides, main)
        if not fair and len(books) >= picks.MIN_BOOKS:
            per = {s: [] for s in sides}
            for b in books:
                inv = {s: 1 / sides[s][b][0] for s in sides}
                tot = sum(inv.values())
                for s in sides:
                    per[s].append(inv[s] / tot)
            fair = {s: picks.median(v) for s, v in per.items()}
        snap = {s: [main.get(s), round(1 / fair[s], 3) if fair.get(s) else None,
                    {b: p for b, (p, _) in bk.items()}] for s, bk in sides.items()}
        ser = px.setdefault(f"{mk}|{desc}", [])
        if ser and ser[-1][1] == snap:
            continue
        ser.append([TS, snap])
        ser[:] = ser[:1] + ser[1:][-23:]   # opener + last 23 changes


def fetch_props(api, sk, ev):
    """One call for all prop markets; if the API rejects the list (e.g. a market not offered
    for that sport), fall back to one market per call and merge. Returns (data, calls)."""
    path = f"/sports/{sk}/events/{ev['id']}/odds"
    label = f"props {ev['home_team']} v {ev['away_team']}"
    kw = dict(regions="au", oddsFormat="decimal", dateFormat="iso")
    if api.low():
        api.errors.append(f"skipped {label}: only {api.remaining} credits left")
        return None, 0
    try:
        return api.get(path, markets=PROP_MARKETS[sk], **kw), 1
    except urllib.error.HTTPError as e:
        if e.code != 422:
            api.errors.append(f"{label}: HTTP {e.code} {e.read()[:200].decode(errors='ignore')}")
            return None, 1
    merged, calls = None, 1
    books = {}
    for m in PROP_MARKETS[sk].split(","):
        if api.low():
            break
        calls += 1
        try:
            d = api.get(path, markets=m, **kw)
        except urllib.error.HTTPError:
            continue
        merged = merged or {k: v for k, v in d.items() if k != "bookmakers"}
        for bk in d.get("bookmakers", []):
            books.setdefault(bk["key"], {**bk, "markets": []})["markets"].extend(bk.get("markets", []))
    if merged is None:
        api.errors.append(f"{label}: no prop markets accepted")
        return None, calls
    merged["bookmakers"] = list(books.values())
    return merged, calls


def main():
    if not KEY:
        sys.exit("ODDS_API_KEY not set")
    if not PASSWORD:
        sys.exit("ODDS_PAGE_PASSWORD not set: refusing to publish unprotected odds")
    os.makedirs(OUT, exist_ok=True)
    prev = load_previous()
    api = Api()

    sports = api.get("/sports")          # free
    active = {s["key"]: s["title"] for s in sports if s.get("active")}
    games = [s["key"] for s in sports if s.get("active") and not s.get("has_outrights")]
    wanted = []
    for pat in SPORTS:  # named sports first, so wildcards never starve them of credits
        if not pat.endswith("*") and pat in active and pat not in wanted:
            wanted.append(pat)
    for pat in SPORTS:
        if pat.endswith("*"):
            wanted += [k for k in games if k.startswith(pat[:-1]) and k not in wanted]

    # ---- game markets ----
    events = []
    for sk in wanted:
        data = api.try_get(sk, f"/sports/{sk}/odds", regions="au", markets=MARKETS,
                           oddsFormat="decimal", dateFormat="iso")
        for ev in data or []:
            ev["sport_title"] = active.get(sk, ev.get("sport_title"))
            events.append(ev)
        # sharp reference price (Pinnacle): used only for the fair price, never shown as a bookie to bet
        if data and sk in SHARP_SPORTS:
            sharp = api.try_get(f"{sk} {SHARP_BOOK}", f"/sports/{sk}/odds", bookmakers=SHARP_BOOK, markets=MARKETS,
                                oddsFormat="decimal", dateFormat="iso") or []
            by_id = {e["id"]: e for e in sharp}
            for ev in data:
                sb = [b for b in by_id.get(ev["id"], {}).get("bookmakers", []) if b["key"] == SHARP_BOOK]
                if sb:
                    ev["sharp"] = sb[0]

    # ---- player props (per game; only charged for markets that come back) ----
    old_props = {e["id"]: e for e in (prev.get("props") or {}).get("events", [])}
    fetched = dict((prev.get("props") or {}).get("fetched", {}))
    props = {}
    prop_calls = 0
    for ev in events:
        sk = ev["sport_key"]
        if sk not in PROP_SPORTS or sk not in PROP_MARKETS:
            continue
        start = parse(ev["commence_time"])
        if not (NOW < start <= NOW + PROPS_WINDOW):
            if ev["id"] in old_props and start > NOW - KEEP_AFTER_START:
                props[ev["id"]] = old_props[ev["id"]]
            continue
        last = fetched.get(ev["id"])
        if last and NOW - parse(last) < PROPS_EVERY and ev["id"] in old_props:
            props[ev["id"]] = old_props[ev["id"]]
            continue
        data, n = fetch_props(api, sk, ev)
        prop_calls += n
        if data is not None and sk in SHARP_SPORTS and not api.low():
            sd = api.try_get(f"props {SHARP_BOOK} {ev['home_team']}", f"/sports/{sk}/events/{ev['id']}/odds",
                             bookmakers=SHARP_BOOK, markets=PROP_MARKETS[sk], oddsFormat="decimal", dateFormat="iso")
            prop_calls += 1
            sb = [b for b in (sd or {}).get("bookmakers", []) if b["key"] == SHARP_BOOK]
            if sb:
                data["sharp"] = sb[0]
        if data is None:
            if ev["id"] in old_props:
                props[ev["id"]] = old_props[ev["id"]]
            continue
        fetched[ev["id"]] = TS
        if data.get("bookmakers"):
            data["sport_title"] = ev["sport_title"]
            data["fetched"] = TS
            props[ev["id"]] = data
    fetched = {k: v for k, v in fetched.items() if k in {e["id"] for e in events}}

    # ---- history ----
    hist = (prev.get("history") or {}).get("events", {})
    for ev in events:
        record(hist, ev)
    for ev in props.values():
        if ev.get("fetched") == TS:
            record(hist, ev)
            record_bookies(hist, ev)
    cutoff = NOW - KEEP_AFTER_START
    hist = {i: e for i, e in hist.items() if parse(e["commence_time"]) > cutoff}

    # ---- horse racing: raw Betfair pull uploaded from a computer in Australia (racing/racing.enc.json) ----
    racing = prev.get("racing")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import fetch_racing, picks as _pk
        rp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "racing", "racing.enc.json")
        ak = os.environ.get("ODDS_ALERTS_KEY", "").strip()
        if os.path.exists(rp) and ak:
            raw = _pk.decrypt_with_key(json.load(open(rp)), ak)
            if NOW - parse(raw["fetched"]) < timedelta(hours=6):
                racing = fetch_racing.process(raw, prev.get("racing"), TS)
                print(f"racing: {len(racing['races'])} races from {raw['fetched']}")
            else:
                print("racing: upload is stale, keeping previous")
    except Exception as e:
        print("racing failed:", type(e).__name__, e)

    # ---- NBA player game logs (BALLDONTLIE, licensed: NBA_STATS_KEY secret) for the players with props ----
    stats = prev.get("stats") or {}
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import player_stats
        nba, note = player_stats.update(stats.get("nba"), list(props.values()), NOW, TS)
        stats = {**stats, "nba": nba}
        print("player stats:", note)
    except Exception as e:
        print("player stats failed:", type(e).__name__, e)

    # ---- hot stats for the free public page (unencrypted on purpose: odds-data/hot.json) ----
    try:
        import hot
        hj = hot.build(list(props.values()), stats.get("nba") or {}, NOW, TS)
        with open(os.path.join(OUT, "hot.json"), "w") as f:
            json.dump(hj, f, separators=(",", ":"))
        print(f"hot stats: {len(hj['items'])} props")
    except Exception as e:
        print("hot stats failed:", type(e).__name__, e)

    bundle = {
        "stats": stats,
        "racing": racing,
        "odds": {
            "updated": TS,
            "credits_remaining": api.remaining,
            "credits_used": api.used,
            "sports_checked": wanted,
            "errors": api.errors,
            "events": sorted(events, key=lambda e: e["commence_time"]),
        },
        "props": {
            "events": sorted(props.values(), key=lambda e: e["commence_time"]),
            "fetched": fetched,
        },
        "history": {"updated": TS, "events": hist},
    }
    with open(os.path.join(OUT, "data.json"), "w") as f:
        json.dump(encrypt(bundle), f)

    # ---- daily picks for the Claude alert (encrypted with a separate key) ----
    alerts_key = os.environ.get("ODDS_ALERTS_KEY", "").strip()
    if alerts_key:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import picks
        summary = picks.compute(events, list(props.values()), hist, NOW)
        summary.update({"generated": TS, "credits_remaining": api.remaining, "errors": api.errors})
        with open(os.path.join(OUT, "picks.json"), "w") as f:
            json.dump(picks.encrypt_with_key(summary, alerts_key), f)
        print(f"picks: {len(summary['overlays'])} overlays, {len(summary['arbs'])} arbs, "
              f"{len(summary['steamers'])} steamers")
    print(f"{len(events)} games across {len(wanted)} sports; props for {len(props)} games "
          f"({prop_calls} prop calls this run); credits remaining: {api.remaining}")
    print(f"sharp ({SHARP_BOOK}): {sum(1 for e in events if e.get('sharp'))} games, "
          f"{sum(1 for e in props.values() if e.get('sharp'))} prop games")
    for e in api.errors:
        print("WARN", e)


if __name__ == "__main__":
    main()
