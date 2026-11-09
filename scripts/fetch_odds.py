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
  SPORTS               game-market sports (default below)
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
          "basketball_nba,basketball_nbl,aussierules_afl,rugbyleague_nrl,soccer_epl,americanfootball_nfl").split(",") if s.strip()]
MARKETS = os.environ.get("MARKETS", "h2h,spreads,totals")
PROP_SPORTS = [s.strip() for s in os.environ.get("PROP_SPORTS",
               "basketball_nba,aussierules_afl,rugbyleague_nrl").split(",") if s.strip()]
PROP_MARKETS = {
    "basketball_nba": "player_points,player_rebounds,player_assists,player_threes,player_points_rebounds_assists",
    "aussierules_afl": "player_disposals,player_goal_scorer_anytime",
    "rugbyleague_nrl": "player_try_scorer_anytime",
}
PROPS_EVERY = timedelta(hours=float(os.environ.get("PROPS_EVERY_HOURS", "6")))
PROPS_WINDOW = timedelta(hours=float(os.environ.get("PROPS_WINDOW_HOURS", "24")))
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
    # one series per side (and per player for props); a line move stays the same series
    who = o.get("description")
    return f"{market}|{who + '|' if who else ''}{o['name']}"


def record(hist, ev):
    pts = {}
    for bk in ev.get("bookmakers", []):
        for m in bk.get("markets", []):
            for o in m.get("outcomes", []):
                if o.get("point") is not None:
                    c = pts.setdefault(okey(m["key"], o), {})
                    c[o["point"]] = c.get(o["point"], 0) + 1
    main_pt = {k: max(c, key=lambda p: (c[p], -abs(p))) for k, c in pts.items()}
    best = {}
    for bk in ev.get("bookmakers", []):
        for m in bk.get("markets", []):
            for o in m.get("outcomes", []):
                k = okey(m["key"], o)
                if k in main_pt and o.get("point") != main_pt[k]:
                    continue
                if k not in best or o["price"] > best[k][0]:
                    best[k] = (o["price"], bk["key"], o.get("point"))
    he = hist.setdefault(ev["id"], {"commence_time": ev["commence_time"], "series": {}})
    he["commence_time"] = ev["commence_time"]
    for k, (price, book, point) in best.items():
        ser = he["series"].setdefault(k, [])
        if not ser or ser[-1][1] != price or ser[-1][3] != point:
            ser.append([TS, price, book, point])
        ser[:] = ser[:1] + ser[1:][-47:]   # opener + last 47 changes


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
    wanted = [s for s in SPORTS if s in active]

    # ---- game markets ----
    events = []
    for sk in wanted:
        data = api.try_get(sk, f"/sports/{sk}/odds", regions="au", markets=MARKETS,
                           oddsFormat="decimal", dateFormat="iso")
        for ev in data or []:
            ev["sport_title"] = active.get(sk, ev.get("sport_title"))
            events.append(ev)

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
        data = api.try_get(f"props {ev['home_team']} v {ev['away_team']}",
                           f"/sports/{sk}/events/{ev['id']}/odds", regions="au",
                           markets=PROP_MARKETS[sk], oddsFormat="decimal", dateFormat="iso")
        prop_calls += 1
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
    cutoff = NOW - KEEP_AFTER_START
    hist = {i: e for i, e in hist.items() if parse(e["commence_time"]) > cutoff}

    bundle = {
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
    print(f"{len(events)} games across {len(wanted)} sports; props for {len(props)} games "
          f"({prop_calls} prop calls this run); credits remaining: {api.remaining}")
    for e in api.errors:
        print("WARN", e)


if __name__ == "__main__":
    main()
