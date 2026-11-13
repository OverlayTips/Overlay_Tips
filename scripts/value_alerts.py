"""Instant value-bet alerts: push each NEW value pick to Jayden's phone (ntfy) right after an odds update.

Reads, from the odds-data folder the odds workflow just wrote:
  hot.json    -> "value" list (NBA player props priced above the market-led fair price)
  picks.json  -> overlays (encrypted with ODDS_ALERTS_KEY)
Remembers what it already sent in alerted.json (kept on the odds-data branch), so each pick alerts once.
A pick alerts again only if its edge has grown by 3+ points or the price got better.

Env: NTFY_TOPIC (required to send), ODDS_ALERTS_KEY (optional, for overlays), OUT (default odds-data),
     TEST=1 sends a test push and exits.
"""
import json, os, re, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

OUT = os.environ.get("OUT", "odds-data")
TOPIC = re.sub(r"[^A-Za-z0-9_-]", "", os.environ.get("NTFY_TOPIC", ""))
MEMBER_TOPIC = re.sub(r"[^A-Za-z0-9_-]", "", os.environ.get("NTFY_MEMBER_TOPIC", ""))   # VIP members' channel
SYD = ZoneInfo("Australia/Sydney")
SITE = "https://overlay.tips"
FOOT = "Overlay Tips | 18+ Gamble responsibly"
MIN_VALUE = 4.0      # % edge needed to alert
MAX_PER_PUSH = 6
MAX_SINGLE = 10**6   # no limit: every new value bet gets its own notification
BANK_UNITS = 100     # stake suggestion = quarter Kelly on a 100-unit bank, 0.5U to 3U


def push(title, lines, click=None, actions=()):
    """Send to Jayden's topic and, if set, the VIP members' topic. True if any send worked."""
    ok = False
    for t in [x for x in (TOPIC, MEMBER_TOPIC) if x]:
        ok = _push(t, title, lines, click, actions) or ok
    if not TOPIC and not MEMBER_TOPIC:
        print("::warning::NTFY_TOPIC secret is empty - value alerts not sent")
    return ok


def _push(topic, title, lines, click=None, actions=(), priority=4):
    body = "\n".join(lines)
    wa = f"{title}\n\n{body}\n\n{FOOT}"
    acts = list(actions)[:2] + [{"action": "view", "label": "Share to WhatsApp",
                                 "url": "https://wa.me/?text=" + urllib.parse.quote(wa[:3500]), "clear": False}]
    msg = {"topic": topic, "title": title, "message": body, "tags": ["moneybag"], "priority": priority, "actions": acts}
    if click:
        msg["click"] = click
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(msg).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        print(f"pushed: {title}" + (" (members)" if topic == MEMBER_TOPIC and topic != TOPIC else ""))
        return True
    except urllib.error.HTTPError as e:
        print(f"::error::ntfy {e.code}: {e.read()[:300]!r}")
        return False


def stake(edge_pct, price):
    if price <= 1:
        return 0.5
    k = (edge_pct / 100) / (price - 1)
    return max(0.5, min(3.0, round(BANK_UNITS * k / 4 * 2) / 2))


def tip(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(SYD).strftime("%a %-I:%M%p").replace("AM", "am").replace("PM", "pm")
    except Exception:
        return ""


def started(iso, now):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")) <= now
    except Exception:
        return False


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def tag(x):
    """Availability marker added by fetch_odds (NBA): starting / questionable / game-time / bench."""
    f = x.get("inj") or ""
    if f in ("Q", "GTD"):
        return f" ⚠️{f}"
    if f == "Bench":
        return " (bench)"
    return " ✅starting" if x.get("starting") else ""


def emoji(key, title=""):
    k, t = str(key or ""), str(title or "").lower()
    if k.startswith("basketball") or t in ("nba", "nbl"):
        return "🏀"
    if k.startswith("soccer") or any(w in t for w in ("league", "liga", "bundesliga", "serie", "ligue", "cup", "epl", "mls", "nations")):
        return "⚽"
    if k.startswith("americanfootball") or t in ("nfl", "ncaaf"):
        return "🏈"
    if k.startswith(("aussierules", "rugby")) or t in ("afl", "nrl"):
        return "🏉"
    if k.startswith("tennis"):
        return "🎾"
    if k.startswith("cricket"):
        return "🏏"
    if k.startswith("icehockey"):
        return "🏒"
    if k.startswith("baseball"):
        return "⚾"
    return "🎯"


def away_at_home(g, key=""):
    """picks.py writes 'Home v Away'; Hot stats writes 'Away @ Home'. Use one style so a game groups once.
    Tennis and fights have no home side, so they keep 'A v B'."""
    if " v " in g and not str(key or "").startswith(("tennis", "mma")):
        h, a = g.split(" v ", 1)
        return f"{a} @ {h}"
    return g


def _frac(x):
    return f"{x[0]}/{x[1]}" if x and x[1] else None


def analysis(v, side):
    """Short case for an NBA/NBL prop: form, hit rates, market v fair, best price, teammate news."""
    out, pt = [], v.get("point")
    games = v.get("games") or []
    if games:
        last = ", ".join(str(g[3]) for g in games[:5])
        out.append(f"📈 Last 5: {last} (line {pt}) · avg {v.get('avg')}")
    hits = [f"{lab} {_frac(v.get(k))}" for lab, k in (("L5", "l5"), ("L10", "l10"), ("Season", "season")) if _frac(v.get(k))]
    if v.get("vs") and v["vs"][1]:
        hits.append(f"v {v.get('opp')} {_frac(v['vs'])}")
    if hits:
        out.append(f"🎯 Hit {side.lower()}: " + " · ".join(hits))
    if v.get("prob") and v.get("mkt"):
        out.append(f"🧮 Our chance {100*v['prob']:.0f}% v market {100*v['mkt']:.0f}% ({v.get('books', '?')} books)")
    pr = sorted((v.get("prices") or {}).items(), key=lambda x: -x[1])
    if len(pr) > 1:
        out.append("💵 " + " · ".join(f"{b} {p:.2f}" for b, p in pr[:4]))
    if v.get("wo"):
        w = v["wo"]
        out.append(f"🩼 {w['n']} out: avg {w['avg']} without him v {w['with']} with ({_frac(w.get('hit')) or '?'} hit, {w.get('g', '?')} games)")
    if v.get("inj"):
        out.append(f"⚠️ {v['player']} listed {v['inj']} — check he plays")
    return out


def collect(now):
    out = []
    hot = load(os.path.join(OUT, "hot.json"), {})
    for v in hot.get("value") or []:
        if (v.get("value") or 0) < MIN_VALUE or started(v.get("start", ""), now):
            continue
        side = "Under" if str(v.get("line", "")).lower().startswith("u") else "Over"
        add = SITE + "/tracker.html?" + urllib.parse.urlencode({
            "add": 1, "player": v["player"], "stat": v["stat"], "side": side,
            "line": v.get("point", ""), "odds": v["price"], "book": v["book"]})
        out.append({"tag": tag(v), "key": f"hot|{v['player']}|{v['stat']}|{v.get('point')}|{side}",
                    "edge": v["value"], "price": v["price"], "start": v.get("start", ""),
                    "text": f"{v['player']}{tag(v)} {v['line']} {v['stat']} @ {v['price']:.2f} {v['book']}",
                    "sel": f"{v['player']}{tag(v)} {v['line']} {v['stat']}", "book": v["book"],
                    "game": v.get("game", ""), "lg": v.get("lg", ""), "emoji": "🏀",
                    "sub": f"fair {100*v.get('prob',0):.0f}%",
                    "why": analysis(v, side),
                    "add": add})
    key = os.environ.get("ODDS_ALERTS_KEY", "").strip()
    bundle = load(os.path.join(OUT, "picks.json"), None)
    if key and bundle and bundle.get("ct"):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import picks
        try:
            summ = picks.decrypt_with_key(bundle, key)
        except Exception as e:
            print(f"::warning::couldn't read picks.json: {e}")
            summ = {}
        for o in summ.get("overlays") or []:
            if o["edge_pct"] < MIN_VALUE or started(o.get("start", ""), now):
                continue
            out.append({"tag": tag(o), "key": f"ov|{o['game']}|{o['market']}|{o['pick']}",
                        "edge": o["edge_pct"], "price": o["price"], "start": o.get("start", ""),
                        "text": f"{o['pick']}{tag(o)} ({o['market']}) @ {o['price']:.2f} {o['book']}",
                        "sel": f"{o['pick']}{tag(o)} ({o['market']})", "book": o["book"],
                        "game": away_at_home(o["game"], o.get("sport_key")), "lg": o.get("sport", ""),
                        "emoji": emoji(o.get("sport_key"), o.get("sport")),
                        "sub": f"fair {o['fair']:.2f}",
                        "add": None})
    return out


ARB_MIN = float(os.environ.get("ARB_MIN", "1.0"))   # % profit needed to alert an arb
BEST_ARB = None
ARB_BOOKIE = os.environ.get("ARB_BOOKIE", "0") == "1"   # bookie v bookie arb alerts (off: Betfair back/lay only)


def collect_arbs(now):
    """Arbs from picks.json: bookie v bookie (stakes for $100 total) and bookie v Betfair (back $100)."""
    key = os.environ.get("ODDS_ALERTS_KEY", "").strip()
    bundle = load(os.path.join(OUT, "picks.json"), None)
    if not (key and bundle and bundle.get("ct")):
        return []
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import picks
    try:
        summ = picks.decrypt_with_key(bundle, key)
    except Exception as e:
        print(f"::warning::couldn't read picks.json: {e}")
        return []
    print("arbs seen: bookie v bookie " + ", ".join(f"{a['margin_pct']}%" for a in summ.get("arbs") or []) +
          " | back/lay " + ", ".join(f"{a['margin_pct']}%" for a in summ.get("layarbs") or []))
    global BEST_ARB
    BEST_ARB = max([a["margin_pct"] for a in (summ.get("layarbs") or []) + (summ.get("arbs") or [] if ARB_BOOKIE else [])], default=None)
    out = []
    for a in (summ.get("arbs") or []) if ARB_BOOKIE else []:
        if a["margin_pct"] < ARB_MIN or started(a.get("start", ""), now):
            continue
        inv = sum(1 / l["price"] for l in a["legs"])
        lines = [f"${100 / l['price'] / inv:.2f} on {l['pick']} @ {l['price']:.2f} {l['book']}" for l in a["legs"]]
        out.append({"key": "arb|" + a["game"] + "|" + a["market"] + "|" + "|".join(sorted(l["pick"] for l in a["legs"])),
                    "margin": a["margin_pct"], "start": a.get("start", ""), "game": away_at_home(a["game"], a.get("sport_key")),
                    "lg": a.get("sport", ""), "emoji": emoji(a.get("sport_key"), a.get("sport")), "market": a["market"],
                    "title": f"⚖️ Arb +{a['margin_pct']:.1f}% · {a['market']}",
                    "lines": lines + [f"= ${100 / inv - 100:.2f} profit on $100 whatever happens"]})
    for a in summ.get("layarbs") or []:
        if a["margin_pct"] < ARB_MIN or started(a.get("start", ""), now):
            continue
        c, B, L = a["comm_pct"] / 100, a["back"], a["lay"]
        ls = 100 * B / (L - c)
        out.append({"key": f"lay|{a['game']}|{a['market']}|{a['pick']}|{a['book']}",
                    "margin": a["margin_pct"], "start": a.get("start", ""), "game": away_at_home(a["game"], a.get("sport_key")),
                    "lg": a.get("sport", ""), "emoji": emoji(a.get("sport_key"), a.get("sport")), "market": a["market"],
                    "title": f"⚖️ Back/lay arb +{a['margin_pct']:.1f}% · {a['pick']}",
                    "lines": [f"Back $100 on {a['pick']} @ {B:.2f} {a['book']}",
                              f"Lay ${ls:.2f} @ {L:.2f} on Betfair (liability ${ls * (L - 1):.2f})",
                              f"= ${a['margin_pct']:.2f} profit whatever happens, after {a['comm_pct']:g}% Betfair commission"]})
    return sorted(out, key=lambda x: -x["margin"])


def send_arbs(state, now):
    arbs, sent = collect_arbs(now), 0
    for a in arbs:
        old = state.get(a["key"])
        if old and a["margin"] < old["edge"] + 1:
            continue
        lg = f" ({a['lg']})" if a.get("lg") else ""
        lines = [f"{a['emoji']} {a['game']}{lg} · {tip(a['start'])} · {a['market']}"] + a["lines"] + \
                ["Prices move fast: check every leg is still there before betting."]
        if push(a["title"], lines, click=SITE + "/odds.html",
                actions=[{"action": "view", "label": "Odds tracker", "url": SITE + "/odds.html", "clear": False}]):
            state[a["key"]] = {"edge": a["margin"], "price": 0, "tag": "", "at": now.isoformat()}
            sent += 1
    print(f"arb alerts: {len(arbs)} at {ARB_MIN}%+, {sent} sent")
    return sent


def main():
    now = datetime.now(timezone.utc)
    if os.environ.get("TEST") == "1":
        push("Value alerts are on ✅", ["You'll get a push the moment an odds update finds a new value bet."])
        return
    state_path = os.path.join(OUT, "alerted.json")
    state = load(state_path, {})
    # forget anything older than 2 days
    cutoff = (now - timedelta(days=2)).isoformat()
    state = {k: v for k, v in state.items() if v.get("at", "") >= cutoff}

    new = []
    for p in sorted(collect(now), key=lambda x: -x["edge"]):
        old = state.get(p["key"])
        cleared = "⚠️" in old.get("tag", "") if old else False   # was questionable, now isn't
        if old and p["edge"] < old["edge"] + 3 and p["price"] <= old["price"] and not (cleared and "⚠️" not in p["tag"]):
            continue
        new.append(p)
    if not new:
        print("value alerts: nothing new")
    else:
        # one notification per bet, so each has its own Share to WhatsApp and Log bet buttons
        top = new[:MAX_SINGLE]
        sent = []
        for p in top:
            lg = f" ({p['lg']})" if p.get("lg") else ""
            title = f"💰 +{p['edge']:.1f}% · {p['sel']}"
            lines = [f"{p['emoji']} {p['game']}{lg} · {tip(p['start'])}",
                     f"${p['price']:.2f} at {p['book']} · {stake(p['edge'], p['price']):g}U · {p['sub']}"] + p.get("why", [])
            acts = [{"action": "view", "label": "Log bet", "url": p["add"], "clear": False}] if p["add"] else []
            acts.append({"action": "view", "label": "Odds tracker", "url": SITE + "/odds.html", "clear": False})
            if push(title, lines, click=SITE + "/odds.html", actions=acts):
                sent.append(p)
        if len(new) > len(top):   # the rest in one summary
            rest = new[len(top):]
            lines = [f"+{p['edge']:.1f}% {p['text']}" for p in rest[:10]]
            if len(rest) > 10:
                lines.append(f"+{len(rest) - 10} more on the odds tracker")
            if push(f"💰 {len(rest)} more value bets", lines, click=SITE + "/odds.html",
                    actions=[{"action": "view", "label": "Odds tracker", "url": SITE + "/odds.html", "clear": False}]):
                sent += rest
        for p in sent:
            state[p["key"]] = {"edge": p["edge"], "price": p["price"], "tag": p["tag"], "at": now.isoformat()}
    arb_sent = 0
    try:
        arb_sent = send_arbs(state, now) or 0
    except Exception as e:
        print(f"::warning::arb alerts failed: {e}")
    # every update: a short status ping to Jayden's own channel only (UPDATE_PING=1), even when nothing is new
    if os.environ.get("UPDATE_PING") == "1" and TOPIC:
        allv = sorted(collect(now), key=lambda x: -x["edge"])
        when = datetime.now(SYD).strftime("%-I:%M%p").lower()
        lines = [f"{len(allv)} value bets live · {len(new)} new this update" + (f" · {arb_sent} new arbs" if arb_sent else ""),
                 f"Best Betfair arb right now: {BEST_ARB:.2f}%" if BEST_ARB is not None else "No Betfair arbs right now"]
        if allv:
            lines.append("Top value:")
            lines += [f"+{p['edge']:.1f}% {p['text']}" for p in allv[:3]]
        try:
            _push(TOPIC, f"📊 Odds updated {when}", lines, click=SITE + "/odds.html", priority=3)
        except Exception as e:
            print(f"::warning::update ping failed: {e}")
    with open(state_path, "w") as f:
        json.dump(state, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
