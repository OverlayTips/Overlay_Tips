"""Auto bet log: every value alert the tracker sends, scored against the closing price and the result.

This is how we find out whether the tracker actually has an edge, instead of guessing from a few bad weeks.

Each alert is logged at the price and bookie it was sent with. Then, every odds update:
  close  - while the game hasn't started, the fair price for that exact selection is refreshed (Pinnacle
           no-vig if it has the line, else the no-vig median across bookies). At tip-off it freezes = closing.
  CLV    - price / closing fair - 1. Beating the close on average is the best sign of a real edge,
           and it shows up after ~100 bets, long before profit and loss settles down.
  result - NBA/NBL/NFL player props from the game logs (NFL anytime TD excepted); H2H / line / total from
           The Odds API scores. Other props (AFL, soccer) are judged on CLV only.
  mute   - any league + market type with 100+ alerts and average CLV below MUTE_CLV stops alerting
           (value_alerts.py reads the list) until it climbs back above it.
  report - once a day (after 9am Melbourne) a push with CLV, record and profit, split by where it's working.

Files (odds-data/): bet_log.json (encrypted with ODDS_ALERTS_KEY), new_alerts.json (from value_alerts.py, not published)
Env: ODDS_ALERTS_KEY, ODDS_PAGE_PASSWORD (read data.json), ODDS_API_KEY (scores), NTFY_TOPIC (report)
     REPORT=1 forces the report now.
"""
import base64, gzip, hashlib, json, os, re, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import picks

OUT = os.environ.get("OUT", "odds-data")
LOG = os.path.join(OUT, "bet_log.json")
NEW = os.path.join(OUT, "new_alerts.json")
KEY = os.environ.get("ODDS_ALERTS_KEY", "").strip()
MEL = ZoneInfo("Australia/Melbourne")
KEEP = timedelta(days=180)
MUTE_MIN, MUTE_CLV = 100, -1.0     # 100+ alerts with average CLV under -1% -> stop alerting that bucket
# one market (e.g. NBA Pts + Reb): 20+ graded bets in the last 14 days losing 15%+ of stakes -> stop alerting it;
# the window rolls, so a mute lifts by itself once the bad run ages out
MK_MUTE_MIN, MK_MUTE_ROI, MK_MUTE_DAYS = 20, -15.0, 14
GRADE_AFTER = timedelta(hours=2, minutes=30)   # look for a result this long after the start (only Final games count)
GIVE_UP = timedelta(days=4)        # no result by then: leave it CLV-only


def _parse(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


# ---------------- storage ----------------
def load_log():
    if not KEY:
        return {"bets": [], "muted": [], "report_day": None}
    try:
        with open(LOG) as f:
            return picks.decrypt_with_key(json.load(f), KEY)
    except Exception:
        return {"bets": [], "muted": [], "report_day": None}


def save_log(log):
    with open(LOG, "w") as f:
        json.dump(picks.encrypt_with_key(log, KEY), f)


def muted():
    """Buckets value_alerts.py should stay quiet on (league + prop/game, and single markets)."""
    log = load_log()
    return set(log.get("muted") or []) | set(log.get("muted_mk") or [])


def mbucket(b):
    """League + market, main and alt lines together: 'NBA|player_points_rebounds', 'NBA|h2h'."""
    return f"{b.get('lg') or b.get('sport_key') or '?'}|{str(b.get('mk') or '?').replace('_alternate', '')}"


def bucket(b):
    kind = "prop" if b.get("desc") else "game"
    return f"{b.get('lg') or b.get('sport_key') or '?'}|{kind}"


def load_data():
    pw = os.environ.get("ODDS_PAGE_PASSWORD", "")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        with open(os.path.join(OUT, "data.json")) as f:
            b = json.load(f)
        d = lambda k: base64.b64decode(b[k])
        key = hashlib.pbkdf2_hmac("sha256", pw.encode(), d("salt"), b.get("iter", 250_000), 32)
        return json.loads(gzip.decompress(AESGCM(key).decrypt(d("iv"), d("ct"), None)))
    except Exception as e:
        print("bet log: couldn't read data.json:", type(e).__name__)
        return {}


# ---------------- closing price ----------------
def _outcomes(bk, mk):
    for m in bk.get("markets", []):
        if m["key"] in (mk, mk + "_alternate"):
            yield from m.get("outcomes", [])


def _opp_point(mk, point):
    return -point if (point is not None and mk.endswith("spreads")) else point


def fair_now(ev, b):
    """(fair decimal price, source, best price at our bookies) for the logged selection, or (None, None, None)."""
    mk, desc, side, pt = b["mk"], b.get("desc") or "", b["side"], b.get("point")

    def two_sided(bk):
        mine = other = None
        for o in _outcomes(bk, mk):
            if (o.get("description") or "") != desc:
                continue
            if o["name"] == side and o.get("point") == pt:
                mine = o["price"]
            elif o["name"] != side and o.get("point") == _opp_point(mk, pt):
                # h2h with a draw has 3 sides: use every other side
                other = (other or 0) + 1 / o["price"] if mk == "h2h" else 1 / o["price"]
        if mine and other:
            inv = 1 / mine
            return inv / (inv + other)
        return None

    prob, src = None, None
    if ev.get("sharp"):
        prob = two_sided(ev["sharp"])
        src = "pin" if prob else None
    if prob is None:
        ps = [p for p in (two_sided(bk) for bk in ev.get("bookmakers", []) if bk["key"] != "betfair_ex_au") if p]
        if len(ps) >= 3:
            prob, src = median(ps), "mkt"
    best = None
    for bk in ev.get("bookmakers", []):
        if picks.MY_BOOKS and bk["key"] not in picks.MY_BOOKS:
            continue
        for o in _outcomes(bk, mk):
            if (o.get("description") or "") == desc and o["name"] == side and o.get("point") == pt:
                best = max(best or 0, o["price"])
    return (round(1 / prob, 3) if prob else None), src, best


# ---------------- results ----------------
STATF = {"player_points": lambda g: g[4], "player_rebounds": lambda g: g[5], "player_assists": lambda g: g[6],
         "player_threes": lambda g: g[7], "player_blocks": lambda g: g[8], "player_steals": lambda g: g[9],
         "player_points_rebounds_assists": lambda g: g[4] + g[5] + g[6], "player_points_rebounds": lambda g: g[4] + g[5],
         "player_points_assists": lambda g: g[4] + g[6], "player_rebounds_assists": lambda g: g[5] + g[6]}
# NFL logs (nfl_stats.py): [date, opp, @/vs, played, pyd, ptd, cmp, patt, int, ryd, ratt, rec, ryds, tgt, team]
NFL_STATF = {"player_pass_yds": lambda g: g[4], "player_pass_tds": lambda g: g[5], "player_pass_completions": lambda g: g[6],
             "player_pass_attempts": lambda g: g[7], "player_pass_interceptions": lambda g: g[8],
             "player_rush_yds": lambda g: g[9], "player_rush_attempts": lambda g: g[10],
             "player_receptions": lambda g: g[11], "player_reception_yds": lambda g: g[12]}


def grade_prop(b, stats):
    lg = {"basketball_nba": "nba", "basketball_nbl": "nbl", "americanfootball_nfl": "nfl"}.get(b.get("sport_key"))
    f = (NFL_STATF if lg == "nfl" else STATF).get(b["mk"])
    if not lg or not f or b.get("point") is None:
        return None
    st = stats.get(lg) or {}
    p = (st.get("players") or {}).get((st.get("by") or {}).get(b["desc"], ""))
    if not p and lg == "nbl" and st.get("raw"):
        # player's props already left the feed: match the bookie name straight against the box-score names
        import nbl_stats
        cands = [{"id": k, "n": v.get("n", ""), "rows": sorted(v.get("g", {}).values(), key=lambda r: r[0], reverse=True)}
                 for k, v in st["raw"].items()]
        c = nbl_stats._match(b["desc"], cands)
        p = {"g": c["rows"]} if c else None
    if not p:
        return None
    day = _parse(b["start"]).date()
    rows = [g for g in p.get("g", []) if abs((datetime.fromisoformat(g[0]).date() - day).days) <= 1]
    if not rows:
        return None
    g = rows[0]
    if g[3] <= 0:
        return "void"   # didn't play
    v = f(g)
    if v == b["point"]:
        return "void"
    over = v > b["point"]
    return "win" if over == (b["side"] == "Over") else "loss"


def fetch_scores(sport_keys):
    key = os.environ.get("ODDS_API_KEY", "").strip()
    out = {}
    if not key:
        return out
    for sk in sport_keys:
        url = f"https://api.the-odds-api.com/v4/sports/{sk}/scores?" + urllib.parse.urlencode(
            {"apiKey": key, "daysFrom": 3, "dateFormat": "iso"})
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "overlay-tips-betlog/1"}), timeout=30) as r:
                for g in json.load(r):
                    if g.get("completed") and g.get("scores"):
                        out[g["id"]] = {s["name"]: float(s["score"]) for s in g["scores"]}
        except Exception as e:
            print(f"bet log: scores {sk} failed: {type(e).__name__}")
    return out


def grade_game(b, sc):
    if not sc or len(sc) < 2:
        return None
    side, pt = b["side"], b.get("point")
    if b["mk"] == "h2h":
        if side == "Draw":
            vals = list(sc.values())
            return "win" if vals[0] == vals[1] else "loss"
        if side not in sc:
            return None
        opp = max(v for k, v in sc.items() if k != side)
        return "win" if sc[side] > opp else "loss"   # a draw loses a team bet
    if b["mk"] == "spreads" and side in sc and pt is not None:
        opp = max(v for k, v in sc.items() if k != side)
        m = sc[side] + pt - opp
        return "void" if m == 0 else ("win" if m > 0 else "loss")
    if b["mk"] == "totals" and pt is not None and side in ("Over", "Under"):
        tot = sum(sc.values())
        if tot == pt:
            return "void"
        return "win" if (tot > pt) == (side == "Over") else "loss"
    return None


# ---------------- report ----------------
def summarise(bets):
    clv = [b["clv"] for b in bets if b.get("clv") is not None]
    gr = [b for b in bets if b.get("result") in ("win", "loss")]
    flat = sum(b["price"] - 1 if b["result"] == "win" else -1 for b in gr)
    staked = sum(b.get("stake") or 1 for b in gr)
    sug = sum(((b["price"] - 1) if b["result"] == "win" else -1) * (b.get("stake") or 1) for b in gr)
    return {"n": len(bets), "clv_n": len(clv), "clv": round(sum(clv) / len(clv), 2) if clv else None,
            "beat": round(100 * sum(1 for c in clv if c > 0) / len(clv)) if clv else None,
            "w": sum(1 for b in gr if b["result"] == "win"), "l": sum(1 for b in gr if b["result"] == "loss"),
            "roi": round(100 * flat / len(gr), 1) if gr else None, "units": round(sug, 1), "staked": round(staked, 1)}


def line(name, s):
    bits = [f"{name}: {s['n']} bets"]
    if s["clv"] is not None:
        bits.append(f"CLV {s['clv']:+.1f}% ({s['beat']}% beat close)")
    if s["w"] + s["l"]:
        bits.append(f"{s['w']}-{s['l']}, ROI {s['roi']:+.1f}%, {s['units']:+g}U")
    return " · ".join(bits)


def report(log, now):
    bets = log["bets"]
    wk = [b for b in bets if _parse(b["t"]) > now - timedelta(days=7)]
    lines = [line("Last 7 days", summarise(wk)), line("All time", summarise(bets)), "", "By league + type (all time):"]
    groups = {}
    for b in bets:
        groups.setdefault(bucket(b), []).append(b)
    for k, bs in sorted(groups.items(), key=lambda x: -len(x[1]))[:8]:
        lines.append(line(k.replace("|", " "), summarise(bs)) + ("  🔇 muted" if k in log.get("muted", []) else ""))
    srcs = {}
    for b in bets:
        srcs.setdefault({"pin": "Pinnacle fair", "mkt": "Bookie consensus"}.get(b.get("src"), b.get("src") or "?"), []).append(b)
    lines += ["", "By fair-price source:"] + [line(k, summarise(v)) for k, v in srcs.items()]
    lines += ["", "CLV > 0 over 100+ bets = real edge. Under 100 bets the results are mostly luck."]
    return lines


def write_public(log, now):
    """odds-data/record.json for the public Tracker record page (record.html). UNENCRYPTED on purpose, so only
    bets that have already started go in: it proves the record without giving away a live tip."""
    done = sorted((b for b in log["bets"] if _parse(b["start"]) <= now), key=lambda b: b["start"])
    groups, srcs = {}, {}
    for b in done:
        groups.setdefault(bucket(b), []).append(b)
        srcs.setdefault("Pinnacle" if b.get("src") in ("pin", "hot-pin") else "Bookie consensus", []).append(b)
    # running profit in units at the suggested stakes, one point per graded bet: [start, units, sel, result]
    units, days = 0.0, []
    for b in done:
        if b.get("result") in ("win", "loss"):
            if not days:
                days.append([b["start"], 0, "Start", ""])
            units += ((b["price"] - 1) if b["result"] == "win" else -1) * (b.get("stake") or 1)
            days.append([b["start"], round(units, 2), b.get("sel", ""), b["result"]])
    # CLV over time, one point per bet with a close: [start, running avg CLV %, this bet's CLV %, bets so far]
    clv_days, tot, cnt = [], 0.0, 0
    for b in done:
        if b.get("clv") is not None:
            tot, cnt = tot + b["clv"], cnt + 1
            clv_days.append([b["start"], round(tot / cnt, 2), round(b["clv"], 2), cnt])
    try:
        with open(os.path.join(OUT, "botd.json")) as f:
            bj = json.load(f)
        botd = {(h.get("sel"), str(h.get("start"))[:16]) for h in ([bj.get("today")] if bj.get("today") else []) + (bj.get("hist") or [])}
    except Exception:
        botd = set()
    def _tier(b):   # older bets have no "tier": props = Daily card, everything else VIP
        return b.get("tier") or ("core" if b.get("desc") else "vip")
    def _botd(b):
        return (b.get("sel"), str(b.get("start"))[:16]) in botd
    def section(rows):
        pts, u = [], 0.0
        for b in rows:
            if b.get("result") in ("win", "loss"):
                if not pts:
                    pts.append([b["start"], 0, "Start", ""])
                u += ((b["price"] - 1) if b["result"] == "win" else -1) * (b.get("stake") or 1)
                pts.append([b["start"], round(u, 2), b.get("sel", ""), b["result"]])
        clv, t, c = [], 0.0, 0
        for b in rows:
            if b.get("clv") is not None:
                t, c = t + b["clv"], c + 1
                clv.append([b["start"], round(t / c, 2), round(b["clv"], 2), c])
        return {"since": rows[0]["start"] if rows else None, "clv_days": clv,
                "periods": {"7d": summarise([b for b in rows if _parse(b["start"]) > now - timedelta(days=7)]),
                            "30d": summarise([b for b in rows if _parse(b["start"]) > now - timedelta(days=30)]),
                            "all": summarise(rows)},
                "units": pts}
    # Daily card: every prop alert (founder picks included) + the bet of the day.
    # Founding: only the founder picks (good matchup + a verdict we like) + the bet of the day.
    members = section([b for b in done if _tier(b) in ("founder", "core") or _botd(b)])
    founders = section([b for b in done if _tier(b) == "founder" or _botd(b)])
    keep =("sel", "game", "lg", "book", "price", "close", "clv", "result", "start", "stake", "edge")
    out = {"updated": now.isoformat().replace("+00:00", "Z"),
           "since": done[0]["start"] if done else None,
           "periods": {"7d": summarise([b for b in done if _parse(b["start"]) > now - timedelta(days=7)]),
                       "30d": summarise([b for b in done if _parse(b["start"]) > now - timedelta(days=30)]),
                       "all": summarise(done)},
           "groups": [{"name": k.replace("|", " "), **summarise(v)} for k, v in sorted(groups.items(), key=lambda x: -len(x[1]))],
           "sources": [{"name": k, **summarise(v)} for k, v in srcs.items()],
           "units": days,
           "clv_days": clv_days,
           "members": members,     # Daily card
           "founders": founders,   # Founding members
           "recent": [{k: b.get(k) for k in keep} for b in reversed(done[-60:])]}
    with open(os.path.join(OUT, "record.json"), "w") as f:
        json.dump(out, f, separators=(",", ":"))


# ---------------- main ----------------
def main():
    if not KEY:
        print("bet log: ODDS_ALERTS_KEY not set, skipping")
        return
    now = datetime.now(timezone.utc)
    log = load_log()
    bets = [b for b in log.get("bets", []) if _parse(b["t"]) > now - KEEP]
    have = {b["id"] for b in bets}

    # 1. new alerts from this run
    try:
        with open(NEW) as f:
            new = json.load(f)
    except Exception:
        new = []
    byid = {b["id"]: b for b in bets}
    for a in new:
        if a.get("ev") and a.get("mk") and a.get("side") and a["id"] not in have:
            bets.append({**a, "t": now.isoformat(), "close": None, "clv": None, "result": None})
            byid[a["id"]] = bets[-1]
            have.add(a["id"])
        elif a.get("tier") == "founder" and a["id"] in byid:
            # re-alerted (e.g. a better price) and this time it went to founders too: it counts as a founder pick,
            # same as the Discord founder tracker
            byid[a["id"]]["tier"] = "founder"
    if os.path.exists(NEW):
        os.remove(NEW)

    # 2. closing prices
    data = load_data()
    evs = {}
    for e in ((data.get("odds") or {}).get("events") or []):
        evs[("g", e["id"])] = e
    for e in ((data.get("props") or {}).get("events") or []):
        evs[("p", e["id"])] = e
    updated = 0
    for b in bets:
        if _parse(b["start"]) <= now or b.get("closed"):
            b["closed"] = True
            continue
        ev = evs.get(("p" if b.get("desc") else "g", b["ev"]))
        if not ev:
            continue
        fair, src, best = fair_now(ev, b)
        if fair:
            b["close"], b["close_src"] = fair, src
            b["clv"] = round((b["price"] / fair - 1) * 100, 2)
            updated += 1
        if best:
            b["close_price"] = best

    # 3. results
    stats = data.get("stats") or {}
    need = sorted({b["sport_key"] for b in bets if not b.get("desc") and b.get("result") is None and b.get("sport_key")
                   and now - GIVE_UP < _parse(b["start"]) < now - GRADE_AFTER})
    scores = fetch_scores(need) if need else {}
    graded, vip_wins = 0, []
    for b in bets:
        if b.get("result") is not None or _parse(b["start"]) > now - GRADE_AFTER:
            continue
        r = grade_prop(b, stats) if b.get("desc") else grade_game(b, scores.get(b["ev"]))
        if r:
            b["result"] = r
            graded += 1
            if r == "win" and (b.get("tier") or ("core" if b.get("desc") else "vip")) == "vip":
                vip_wins.append(b)   # for the Discord upgrade teaser
        elif now - _parse(b["start"]) > GIVE_UP:
            b["result"] = "ungraded"

    # 4. mute buckets that don't beat the close
    groups = {}
    for b in bets:
        if b.get("clv") is not None and b.get("closed"):
            groups.setdefault(bucket(b), []).append(b["clv"])
    mutes = sorted(k for k, v in groups.items() if len(v) >= MUTE_MIN and sum(v) / len(v) < MUTE_CLV)
    for k in set(mutes) - set(log.get("muted") or []):
        print(f"bet log: muting {k} (avg CLV {sum(groups[k]) / len(groups[k]):.2f}% over {len(groups[k])})")

    # 5. mute single markets that are losing money lately
    since = now - timedelta(days=MK_MUTE_DAYS)
    pl = {}
    for b in bets:
        if b.get("result") in ("win", "loss") and _parse(b["start"]) >= since:
            st = b.get("stake") or 1
            r = pl.setdefault(mbucket(b), [0, 0.0, 0.0])
            r[0] += 1
            r[1] += st
            r[2] += (b["price"] - 1) * st if b["result"] == "win" else -st
    mk_mutes = sorted(k for k, (n, st, u) in pl.items() if n >= MK_MUTE_MIN and st and 100 * u / st < MK_MUTE_ROI)
    for k in set(mk_mutes) - set(log.get("muted_mk") or []):
        n, st, u = pl[k]
        print(f"bet log: muting market {k} ({n} bets, ROI {100 * u / st:+.1f}% over {MK_MUTE_DAYS} days)")
    for k in set(log.get("muted_mk") or []) - set(mk_mutes):
        print(f"bet log: unmuting market {k}")

    log = {"bets": bets, "muted": mutes, "muted_mk": mk_mutes, "report_day": log.get("report_day"), "discord_day": log.get("discord_day"),
           "weekly_day": log.get("weekly_day"), "league_day": log.get("league_day"), "tease": log.get("tease") or {}}

    # 5. daily report
    local = now.astimezone(MEL)
    today = local.date().isoformat()
    if os.environ.get("REPORT") == "1" or (local.hour >= 9 and log["report_day"] != today and bets):
        lines = report(log, now)
        print("\n".join(lines))
        topic = re.sub(r"[^A-Za-z0-9_-]", "", os.environ.get("NTFY_TOPIC", ""))
        if topic:
            import value_alerts
            value_alerts._push(topic, "📈 Tracker report", lines, click=value_alerts.SITE + "/odds.html", priority=3)
        log["report_day"] = today

    save_log(log)
    try:
        write_public(log, now)
    except Exception as e:
        print("bet log: public record failed:", type(e).__name__, e)
    try:   # once a day from 9am Melbourne: yesterday's results for both systems into Discord #results
        import discord_post
        if discord_post.enabled() and local.hour >= 9 and log.get("discord_day") != today:
            with open(os.path.join(OUT, "record.json")) as f:
                rec = json.load(f)
            if discord_post.daily_results(rec, now):
                log["discord_day"] = today
                save_log(log)
        # and yesterday's units by league (NBA / NFL / NBL, only the ones with action)
        if discord_post.enabled() and local.hour >= 9 and log.get("league_day") != today:
            if discord_post.league_summary(bets, now):
                log["league_day"] = today
                save_log(log)
        # Mondays: last week's recap for both systems
        if discord_post.enabled() and local.weekday() == 0 and local.hour >= 9 and log.get("weekly_day") != today:
            with open(os.path.join(OUT, "record.json")) as f:
                rec = json.load(f)
            if discord_post.weekly_recap(rec, now):
                log["weekly_day"] = today
                save_log(log)
        # VIP-only winners: an upgrade teaser in #chat, at most 4 a day
        if discord_post.enabled() and vip_wins:
            t = log["tease"] if log["tease"].get("day") == today else {"day": today, "n": 0}
            for b in sorted(vip_wins, key=lambda b: -(b["price"] - 1) * (b.get("stake") or 1)):
                if t["n"] >= 4:
                    break
                if discord_post.teaser(b):
                    t["n"] += 1
            log["tease"] = t
            save_log(log)
    except Exception as e:
        print("bet log: discord results failed:", type(e).__name__, e)
    print(f"bet log: {len(bets)} bets ({len(new)} new alerts), {updated} closing prices updated, {graded} graded, "
          f"{len(mutes)} muted")


if __name__ == "__main__":
    main()
