"""Overlay Tips Odds Fantasy (free to play, members only): rounds, odds multipliers and scoring.

Two parts:
  build_pool(...)  called from fetch_odds.py every run. Turns the NBA player props into one round per
                   Melbourne day: every player with a points line, his 20+ points price and the multiplier.
                   Written to odds-data/fantasy_pool.json (not published; the sync below reads it).
  python scripts/fantasy.py
                   runs after fetch_odds.py in the odds workflow. Copies the pool into Supabase until the
                   round locks (first tip-off), then never touches the pool again, so multipliers are
                   frozen at lock. After lock it pulls box scores from BALLDONTLIE (NBA_STATS_KEY) for the
                   round's players and saves each player's base points; the database adds up the ladders.

Env for the sync: SUPABASE_SERVICE_KEY (secret / service_role key), NBA_STATS_KEY, SUPABASE_URL (optional).
Scoring (rulebook): point 1, rebound 1.5, assist 1.5, steal 3, block 3; x multiplier; Overlay Pick x2.
Multiplier from the 20+ points price at lock: under $2 = 1x, $2-3.99 = 1.5x, $4-7.99 = 2x, $8+ = 3x.
When no bookie prices 20+ for a player, the price is estimated from his main points line (marked "e").
Everything is best effort: a failure here never stops the odds run.
"""
import json, math, os, statistics, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MEL = ZoneInfo("Australia/Melbourne")
ET = ZoneInfo("America/New_York")
SB_URL = os.environ.get("SUPABASE_URL", "https://enldqvdxygppxwrauqlw.supabase.co").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
BDL = "https://api.balldontlie.io/v1"
BDL_KEY = os.environ.get("NBA_STATS_KEY", "").strip()
OUT = os.environ.get("OUT_DIR", "odds-data")
POOL_FILE = os.path.join(OUT, "fantasy_pool.json")

SKIP_BOOKS = {"betfair_ex_au", "pinnacle"}
LINE = 19.5                      # "20+ points" = Over 19.5
MIN_LINE = 4.5                   # players with a main points line below this aren't in the pool
WEIGHTS = {"pts": 1, "reb": 1.5, "ast": 1.5, "stl": 3, "blk": 3}
FINAL_AFTER = timedelta(hours=5)  # a game this long after tip-off with no box score = treat as done


def mult(price):
    if price < 2:
        return 1
    if price < 4:
        return 1.5
    if price < 8:
        return 2
    return 3


def base_points(s):
    return round(sum(WEIGHTS[k] * (s.get(k) or 0) for k in WEIGHTS), 1)


def _parse(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def estimate_20(line):
    """No 20+ price: rough fair price from the main points line (normal, sd grows with the line), with a
    ladder-like margin, capped so nobody gets a silly number."""
    sd = max(4.0, 0.33 * line + 1.5)
    p = 1 - _phi((LINE + 0.5 - (line + 0.5)) / sd)
    p = min(max(p, 0.02), 0.97)
    return round(min(max(0.9 / p, 1.01), 51.0), 2)


def build_pool(prop_events, events, nba_stats, now, ts, avail=None):
    """prop_events/events: Odds API events (props / game markets). nba_stats: bundle stats["nba"]."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import picks, player_stats
    teams = (nba_stats or {}).get("teams") or {}
    roster = {k: {"id": v[0], "team": v[1], "n": v[2]} for k, v in (((nba_stats or {}).get("ids") or {}).get("map") or {}).items()}
    rounds = {}

    # every NBA game (props or not) decides the round's games and lock time
    for ev in events or []:
        if ev.get("sport_key") != "basketball_nba":
            continue
        start = _parse(ev["commence_time"])
        if start < now - timedelta(hours=12):
            continue
        day = start.astimezone(MEL).date().isoformat()
        r = rounds.setdefault(day, {"games": {}, "pool": {}})
        r["games"][ev["id"]] = {"id": ev["id"], "home": ev.get("home_team"), "away": ev.get("away_team"),
                                "h": teams.get(ev.get("home_team"), ""), "a": teams.get(ev.get("away_team"), ""),
                                "start": ev["commence_time"]}

    for ev in prop_events or []:
        if ev.get("sport_key") != "basketball_nba":
            continue
        start = _parse(ev["commence_time"])
        if start < now - timedelta(hours=12):
            continue
        day = start.astimezone(MEL).date().isoformat()
        r = rounds.setdefault(day, {"games": {}, "pool": {}})
        r["games"].setdefault(ev["id"], {"id": ev["id"], "home": ev.get("home_team"), "away": ev.get("away_team"),
                                         "h": teams.get(ev.get("home_team"), ""), "a": teams.get(ev.get("away_team"), ""),
                                         "start": ev["commence_time"]})
        main = picks.main_points(ev)
        over20, mainline = {}, {}
        for bk in ev.get("bookmakers", []):
            if bk["key"] in SKIP_BOOKS:
                continue
            for m in bk.get("markets", []):
                if m["key"] not in ("player_points", "player_points_alternate"):
                    continue
                for o in m.get("outcomes", []):
                    d = o.get("description")
                    if not d or o.get("name") != "Over" or o.get("point") is None:
                        continue
                    if abs(float(o["point"]) - LINE) < 1e-6:
                        over20.setdefault(d, []).append(float(o["price"]))
                    mp = main.get(("player_points", d, "Over"))
                    if m["key"] == "player_points" and mp is not None and o["point"] == mp:
                        mainline[d] = float(mp)
        home_ab, away_ab = teams.get(ev.get("home_team"), ""), teams.get(ev.get("away_team"), "")
        for d in set(over20) | set(mainline):
            line = mainline.get(d)
            if d not in over20 and (line is None or line < MIN_LINE):
                continue
            if avail is not None:
                try:
                    if avail.check(d)["drop"]:
                        continue
                except Exception:
                    pass
            if d in over20:
                price, est = round(statistics.median(over20[d]), 2), False
            else:
                price, est = estimate_20(line), True
            key = player_stats.norm(d)
            a = player_stats._match(d, roster) if roster else None
            if not a:
                continue   # no stats id = can't be scored, so leave him out
            t = a.get("team", "")
            if t and t not in (home_ab, away_ab):
                t = ""   # stale team (traded): don't show the wrong one
            opp = away_ab if t == home_ab else home_ab if t == away_ab else ""
            r["pool"][key] = {"k": key, "n": d, "id": a["id"], "t": t, "opp": opp,
                              "home": t == home_ab if t else None, "ev": ev["id"], "start": ev["commence_time"],
                              "line": line, "o": price, "e": est, "m": mult(price)}

    out = []
    for day, r in sorted(rounds.items()):
        if not r["games"]:
            continue
        games = sorted(r["games"].values(), key=lambda g: g["start"])
        pool = sorted(r["pool"].values(), key=lambda p: (p["start"], p["o"]))
        nice = datetime.fromisoformat(day).strftime("%a %-d %b")
        out.append({"id": f"nba-{day}", "sport": "nba", "date": day, "label": f"NBA · {nice}",
                    "lock": games[0]["start"], "games": games, "pool": pool})
    return {"updated": ts, "rounds": out}


# ---------------- Supabase (service key) ----------------
def _sb(method, path, body=None, prefer=None):
    h = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    req = urllib.request.Request(f"{SB_URL}/rest/v1/{path}", method=method, headers=h,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read()
        return json.loads(txt) if txt else None


def _bdl(path, params):
    url = f"{BDL}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": BDL_KEY, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def _bdl_pages(path, params, limit=20):
    cursor, out = None, []
    for _ in range(limit):
        p = list(params) + [("per_page", 100)] + ([("cursor", cursor)] if cursor else [])
        j = _bdl(path, p)
        out += j.get("data") or []
        cursor = (j.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
    return out


def _mins(m):
    try:
        return int(float(str(m or "0").split(":")[0]))
    except ValueError:
        return 0


def score_round(rnd, now):
    """Box scores for the round's pool players. Returns (results, all_final)."""
    pool = rnd.get("pool") or []
    games = rnd.get("games") or []
    et_dates = sorted({_parse(g["start"]).astimezone(ET).date().isoformat() for g in games})
    want = {}   # BDL id -> (pool key, ET date of his game)
    for p in pool:
        if p.get("id"):
            want[int(p["id"])] = (p["k"], _parse(p["start"]).astimezone(ET).date().isoformat())
    results = dict(rnd.get("results") or {})
    ids = list(want)
    for i in range(0, len(ids), 25):
        chunk = ids[i:i + 25]
        rows = _bdl_pages("/stats", [("dates[]", d) for d in et_dates] + [("player_ids[]", x) for x in chunk])
        for s in rows:
            pid = (s.get("player") or {}).get("id")
            g = s.get("game") or {}
            if pid not in want or str(g.get("date", ""))[:10] != want[pid][1]:
                continue
            st = {k: int(s.get(k) or 0) for k in WEIGHTS}
            st["min"] = _mins(s.get("min"))
            st["b"] = base_points(st)
            st["f"] = str(g.get("status", "")).startswith("Final")
            results[want[pid][0]] = st

    # is every game in the round finished?
    finals = {}
    try:
        for g in _bdl_pages("/games", [("dates[]", d) for d in et_dates], limit=3):
            finals[(g.get("home_team") or {}).get("abbreviation"), (g.get("visitor_team") or {}).get("abbreviation")] = \
                str(g.get("status", "")).startswith("Final")
    except Exception as e:
        print("  games check failed:", type(e).__name__, e)
    done = True
    for g in games:
        f = finals.get((g.get("h"), g.get("a")))
        if f is None:
            f = now > _parse(g["start"]) + FINAL_AFTER * 2   # game missing from BDL: give it up eventually
        done = done and f
    if done:   # anyone picked who didn't play scores 0
        for p in pool:
            results.setdefault(p["k"], {"pts": 0, "reb": 0, "ast": 0, "stl": 0, "blk": 0, "min": 0, "b": 0, "f": True})
            results[p["k"]]["f"] = True
    return results, done


def sync(now=None):
    now = now or datetime.now(timezone.utc)
    if not SB_KEY:
        print("::warning::Fantasy: add the SUPABASE_SERVICE_KEY secret to run Odds Fantasy rounds")
        return
    try:
        with open(POOL_FILE) as f:
            built = json.load(f)
    except FileNotFoundError:
        built = {"rounds": []}

    ids = ",".join(r["id"] for r in built["rounds"])
    have = {}
    if ids:
        for r in _sb("GET", f"fantasy_rounds?select=id,status,lock_at&id=in.({ids})") or []:
            have[r["id"]] = r

    # 1. open rounds: refresh pool + multipliers until lock (never after)
    for r in built["rounds"]:
        lock = _parse(r["lock"])
        old = have.get(r["id"])
        if now >= lock:
            continue                      # locked: pool frozen (and a round first seen after lock never opens)
        if old and old["status"] != "open":
            continue
        if not r["pool"]:
            continue                      # no props yet: open it once players are priced
        row = {"id": r["id"], "sport": r["sport"], "label": r["label"], "round_date": r["date"],
               "lock_at": r["lock"], "games": r["games"], "pool": r["pool"], "status": "open",
               "updated_at": now.isoformat()}
        _sb("POST", "fantasy_rounds?on_conflict=id", [row], prefer="resolution=merge-duplicates,return=minimal")
        print(f"fantasy {r['id']}: {len(r['pool'])} players, locks {lock.astimezone(MEL):%a %-I:%M%p}")

    # 2. locked rounds still being played: mark locked, pull box scores
    live = _sb("GET", "fantasy_rounds?select=*&status=neq.final&lock_at=lt." + urllib.parse.quote(now.isoformat())) or []
    for rnd in live:
        if not BDL_KEY:
            print("  fantasy scoring needs NBA_STATS_KEY")
            break
        try:
            results, done = score_round(rnd, now)
        except Exception as e:
            print(f"  fantasy {rnd['id']} scoring failed:", type(e).__name__, e)
            continue
        status = "final" if done else "locked"
        _sb("PATCH", f"fantasy_rounds?id=eq.{rnd['id']}",
            {"results": results, "status": status, "updated_at": now.isoformat()}, prefer="return=minimal")
        print(f"fantasy {rnd['id']}: {len(results)} players scored, {status}")


if __name__ == "__main__":
    try:
        sync()
    except urllib.error.HTTPError as e:
        print("fantasy sync failed: HTTP", e.code, e.read()[:300].decode(errors="ignore"))
    except Exception as e:
        print("fantasy sync failed:", type(e).__name__, e)
