"""Racing record: every racing pick the tracker sends, graded at Betfair SP, for the Racing graph on record.html.

What gets logged (odds-data/racing_log.json, encrypted with ODDS_ALERTS_KEY):
  top    - the Betfair models' morning top picks (race_tops), exactly as pushed: logged in the run where
           value_alerts.py sent the morning racing push (alerted.json "race_am" = today).
  value  - racing value picks (Betfair back price over the Hub's rated price), the first time each one shows up.
           These need a fresh racing upload from the computer in Australia, so they're rarer.

Grading, 1U level stakes at Betfair SP (before Betfair's commission):
  1. Betfair's public BSP result files (promo.betfair.com/betfairsp/prices/dwbfpricesauswin<ddmmyyyy>.csv):
     winner + SP for every Australian horse race. Tried once an hour per race day, from the day after.
  2. Fallback: settled results in the racing upload (racing/racing.enc.json "results", see fetch_racing.py).
     Greyhounds are graded from this only.
  A runner missing from a race that's in the results = scratched -> void. Nothing found after GIVE_UP -> ungraded.

Public output: odds-data/racing_record.json (unencrypted, graded and past races only).
Env: ODDS_ALERTS_KEY, OUT (default odds-data).
"""
import csv, io, json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone, date as _date
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import picks

OUT = os.environ.get("OUT", "odds-data")
LOG = os.path.join(OUT, "racing_log.json")
PUB = os.path.join(OUT, "racing_record.json")
UPLOAD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "racing", "racing.enc.json")
KEY = os.environ.get("ODDS_ALERTS_KEY", "").strip()
SYD = ZoneInfo("Australia/Sydney")
BSP_URL = "https://promo.betfair.com/betfairsp/prices/dwbfpricesauswin{}.csv"
RETRY = timedelta(hours=1)    # BSP file not up yet / missing races: try again this long after
GIVE_UP = timedelta(days=5)   # still no result this long after the race day: leave it ungraded
KEEP = timedelta(days=365)


def _now():
    return datetime.now(timezone.utc)


def _mid(m):
    """'1.265395319' and '265395319' are the same market."""
    s = str(m or "").strip()
    return s[2:] if s.startswith("1.") else s


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------- storage ----------------
def load_log():
    try:
        with open(LOG) as f:
            return picks.decrypt_with_key(json.load(f), KEY)
    except Exception:
        return {"picks": [], "tops_day": None, "tried": {}}


def save_log(log):
    with open(LOG, "w") as f:
        json.dump(picks.encrypt_with_key(log, KEY), f)


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


# ---------------- 1. log new picks ----------------
def collect(log, now):
    bundle = _load_json(os.path.join(OUT, "picks.json"))
    if not (bundle and bundle.get("ct")):
        return 0
    summ = picks.decrypt_with_key(bundle, KEY) or {}
    have = {p["id"] for p in log["picks"]}
    today = now.astimezone(SYD).date().isoformat()
    added = 0

    def add(p):
        nonlocal added
        if p["id"] not in have:
            log["picks"].append(p)
            have.add(p["id"])
            added += 1

    # morning top picks: freeze the list that was actually pushed today
    state = _load_json(os.path.join(OUT, "alerted.json")) or {}
    tops = summ.get("race_tops") or {}
    if ((state.get("race_am") or {}).get("date") == today and log.get("tops_day") != today
            and tops.get("date") == today):
        for x in tops.get("items") or []:
            if not (x.get("mid") and x.get("sid")):
                continue
            add({"id": f"top|{_mid(x['mid'])}|{x['sid']}", "type": "top", "kind": x["kind"], "date": today,
                 "mid": _mid(x["mid"]), "sid": x["sid"], "venue": x.get("venue"), "num": x.get("num"),
                 "no": x.get("no"), "name": x["name"], "rated": x["rated"], "t": now.isoformat()})
        log["tops_day"] = today

    # racing value picks (live Betfair back price over the rated price)
    for o in summ.get("racing_value") or []:
        if not (o.get("mid") and o.get("sid")):
            continue
        d = datetime.fromisoformat(str(o["start"]).replace("Z", "+00:00")).astimezone(SYD).date().isoformat()
        add({"id": f"value|{_mid(o['mid'])}|{o['sid']}", "type": "value",
             "kind": "Horse" if o.get("sport") == "Racing" else "Greyhound", "date": d, "start": o["start"],
             "mid": _mid(o["mid"]), "sid": o["sid"], "venue": o.get("game"), "num": None, "no": None,
             "name": o["pick"].split(". ", 1)[-1], "rated": o.get("fair"), "price": o.get("price"),
             "edge": o.get("edge_pct"), "t": now.isoformat()})
    return added


# ---------------- 2. results ----------------
def bsp_results(day_iso):
    """{market: {sel: {"win": bool, "sp": float|None}}} from Betfair's BSP file for that day, or None."""
    d = _date.fromisoformat(day_iso)
    req = urllib.request.Request(BSP_URL.format(d.strftime("%d%m%Y")), headers={"User-Agent": "overlay-tips/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return None
    out = {}
    for row in rows:
        row = {str(k).strip().upper(): (v or "").strip() for k, v in row.items() if k}
        m, s = _mid(row.get("EVENT_ID")), row.get("SELECTION_ID")
        if not (m and s and s.isdigit()):
            continue
        out.setdefault(m, {})[int(s)] = {"win": row.get("WIN_LOSE") == "1", "sp": _num(row.get("BSP"))}
    return out


def upload_results():
    """Same shape as bsp_results(), from the racing upload's settled markets (fetch_racing.py)."""
    if not os.path.exists(UPLOAD):
        return {}
    try:
        raw = picks.decrypt_with_key(_load_json(UPLOAD), KEY)
    except Exception:
        return {}
    out = {}
    for mid, mk in (raw.get("results") or {}).items():
        if mk.get("status") != "CLOSED":
            continue
        rs = {}
        for sid, r in (mk.get("runners") or {}).items():
            if r.get("status") in ("WINNER", "LOSER"):
                rs[int(sid)] = {"win": r["status"] == "WINNER", "sp": _num(r.get("sp"))}
        if rs:
            out[_mid(mid)] = rs
    return out


def grade(log, now):
    today = now.astimezone(SYD).date()
    pend = [p for p in log["picks"] if not p.get("result")]
    if not pend:
        return 0
    up = upload_results()
    tried = log.setdefault("tried", {})
    files = {}
    for d in sorted({p["date"] for p in pend}):
        if _date.fromisoformat(d) >= today:
            continue
        last = tried.get(d)
        if last and now - datetime.fromisoformat(last) < RETRY:
            continue
        tried[d] = now.isoformat()
        try:
            files[d] = bsp_results(d) or {}
            print(f"racing log: BSP file {d}: {len(files[d])} races")
        except Exception as e:
            print(f"racing log: BSP file {d} failed: {type(e).__name__} {e}")
    n = 0
    for p in pend:
        res = None
        for src_name, src in (("bsp", files.get(p["date"]) or {}), ("upload", up)):
            race = src.get(p["mid"])
            if race is None:
                continue
            r = race.get(int(p["sid"]))
            if r is None:
                res = {"result": "void", "src": src_name}       # race settled without this runner: scratched
            else:
                res = {"result": "win" if r["win"] else "loss", "bsp": r["sp"], "src": src_name}
            break
        if res is None and now.astimezone(SYD).date() - _date.fromisoformat(p["date"]) > GIVE_UP:
            res = {"result": "ungraded"}
        if res:
            p.update(res, graded=now.isoformat())
            n += 1
    log["tried"] = {d: t for d, t in tried.items() if _date.fromisoformat(d) > today - GIVE_UP - timedelta(days=2)}
    return n


# ---------------- 3. public record ----------------
def _profit(p):
    if p.get("result") == "win" and p.get("bsp"):
        return p["bsp"] - 1
    if p.get("result") == "loss":
        return -1.0
    return None


def summarise(ps):
    g = [p for p in ps if _profit(p) is not None]
    w = sum(p["result"] == "win" for p in g)
    units = sum(_profit(p) for p in g)
    over = [p["bsp"] / p["rated"] - 1 for p in g if p.get("bsp") and p.get("rated")]
    return {"n": len(ps), "w": w, "l": len(g) - w, "strike": round(100 * w / len(g), 1) if g else None,
            "units": round(units, 2), "roi": round(100 * units / len(g), 1) if g else None,
            "sp_over": round(100 * sum(over) / len(over), 1) if over else None,
            "pending": sum(not p.get("result") for p in ps)}


def _when(p):
    return p.get("start") or f"{p['date']}T03:00:00Z"   # no start time for top picks: middle of the race day


def write_public(log, now):
    today = now.astimezone(SYD).date().isoformat()
    done = sorted((p for p in log["picks"] if p["date"] < today or p.get("result")),
                  key=lambda p: (_when(p), str(p.get("venue")), p.get("num") or 0))
    def since(days):
        cut = (now.astimezone(SYD).date() - timedelta(days=days)).isoformat()
        return [p for p in done if p["date"] > cut]
    groups = {}
    for p in done:
        groups.setdefault(f"{'Horses' if p['kind'] == 'Horse' else 'Greyhounds'} · "
                          f"{'model top picks' if p['type'] == 'top' else 'value picks'}", []).append(p)
    units, pts = 0.0, []
    for p in done:
        pr = _profit(p)
        if pr is None:
            continue
        if not pts:
            pts.append([_when(p), 0, "Start", ""])
        units += pr
        pts.append([_when(p), round(units, 2), f"{p['name']} ({p.get('venue') or ''}"
                    f"{' R' + str(p['num']) if p.get('num') else ''})", p["result"]])
    keep = ("type", "kind", "date", "venue", "num", "no", "name", "rated", "price", "bsp", "result")
    out = {"updated": now.isoformat().replace("+00:00", "Z"),
           "since": done[0]["date"] if done else None,
           "periods": {"7d": summarise(since(7)), "30d": summarise(since(30)), "all": summarise(done)},
           "groups": [{"name": k, **summarise(v)} for k, v in sorted(groups.items(), key=lambda x: -len(x[1]))],
           "units": pts,
           "recent": [{k: p.get(k) for k in keep} for p in reversed(done[-80:])]}
    with open(PUB, "w") as f:
        json.dump(out, f, separators=(",", ":"))


def main():
    if not KEY:
        print("racing log: ODDS_ALERTS_KEY not set, skipping")
        return
    now = _now()
    log = load_log()
    log["picks"] = [p for p in log.get("picks", []) if datetime.fromisoformat(p["t"]) > now - KEEP]
    added = collect(log, now)
    graded = grade(log, now)
    save_log(log)
    write_public(log, now)
    print(f"racing log: {added} new, {graded} graded, {len(log['picks'])} tracked")


if __name__ == "__main__":
    main()
