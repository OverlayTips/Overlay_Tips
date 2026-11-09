#!/usr/bin/env python3
"""Pull Aussie bookie odds from The Odds API and keep a rolling price history.

Writes two files into OUT_DIR (default: ./odds-data):
  odds.json     current prices for every upcoming event, every bookie
  history.json  best-price snapshots per outcome, used for line movement

Env:
  ODDS_API_KEY   required
  SPORTS         comma list of sport keys (default below)
  MARKETS        default h2h,spreads,totals
  MIN_CREDITS    stop fetching if remaining credits drop below this (default 50)
"""
import json, os, sys, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

API = os.environ.get("ODDS_API_BASE", "https://api.the-odds-api.com/v4")
KEY = os.environ.get("ODDS_API_KEY", "").strip()
SPORTS = [s.strip() for s in os.environ.get("SPORTS",
          "basketball_nba,basketball_nbl,aussierules_afl,rugbyleague_nrl,soccer_epl,americanfootball_nfl").split(",") if s.strip()]
MARKETS = os.environ.get("MARKETS", "h2h,spreads,totals")
MIN_CREDITS = int(os.environ.get("MIN_CREDITS", "50"))
OUT = os.environ.get("OUT_DIR", "odds-data")
KEEP_AFTER_START = timedelta(hours=6)   # drop events this long after tip-off


def get(path, **params):
    params["apiKey"] = KEY
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "overlay-tips-odds/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r), r.headers


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load(name, default):
    try:
        with open(os.path.join(OUT, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def okey(market, outcome):
    # spreads/totals outcomes are keyed by side only, so a line move (e.g. -4.5 -> -5.5) stays one series
    return f"{market}|{outcome['name']}"


def main():
    if not KEY:
        sys.exit("ODDS_API_KEY not set")
    os.makedirs(OUT, exist_ok=True)
    ts = now_iso()

    # /sports is free: only spend credits on sports that are in season
    sports, _ = get("/sports")
    active = {s["key"]: s["title"] for s in sports if s.get("active")}
    wanted = [s for s in SPORTS if s in active]

    events, remaining, used, errors = [], None, None, []
    for sk in wanted:
        if remaining is not None and remaining < MIN_CREDITS:
            errors.append(f"skipped {sk}: only {remaining} credits left")
            continue
        try:
            data, h = get(f"/sports/{sk}/odds", regions="au", markets=MARKETS,
                          oddsFormat="decimal", dateFormat="iso")
        except urllib.error.HTTPError as e:
            errors.append(f"{sk}: HTTP {e.code} {e.read()[:200].decode(errors='ignore')}")
            continue
        remaining = int(float(h.get("x-requests-remaining", 0) or 0))
        used = int(float(h.get("x-requests-used", 0) or 0))
        for ev in data:
            ev["sport_title"] = active.get(sk, ev.get("sport_title"))
            events.append(ev)

    # ---- history: best price per outcome per snapshot ----
    hist = load("history.json", {"events": {}})
    cutoff = datetime.now(timezone.utc) - KEEP_AFTER_START
    for ev in events:
        # for line/total markets, compare bookies only at the most common (main) point
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
        he = hist["events"].setdefault(ev["id"], {"commence_time": ev["commence_time"], "series": {}})
        he["commence_time"] = ev["commence_time"]
        for k, (price, book, point) in best.items():
            ser = he["series"].setdefault(k, [])
            if not ser or ser[-1][1] != price or ser[-1][3] != point:
                ser.append([ts, price, book, point])
            ser[:] = ser[:1] + ser[1:][-47:]   # keep the opener + last 47 changes
    hist["events"] = {i: e for i, e in hist["events"].items()
                      if datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) > cutoff}
    hist["updated"] = ts

    out = {
        "updated": ts,
        "credits_remaining": remaining,
        "credits_used": used,
        "sports_checked": wanted,
        "errors": errors,
        "events": sorted(events, key=lambda e: e["commence_time"]),
    }
    with open(os.path.join(OUT, "odds.json"), "w") as f:
        json.dump(out, f, separators=(",", ":"))
    with open(os.path.join(OUT, "history.json"), "w") as f:
        json.dump(hist, f, separators=(",", ":"))
    print(f"{len(events)} events across {len(wanted)} sports; credits remaining: {remaining}")
    for e in errors:
        print("WARN", e)


if __name__ == "__main__":
    main()
