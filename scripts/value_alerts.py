"""Instant value-bet alerts: push each NEW value pick to Jayden's phone (ntfy) right after an odds update.

Reads, from the odds-data folder the odds workflow just wrote:
  hot.json    -> "value" list (NBA, NBL and NFL player props priced above the market-led fair price)
  picks.json  -> overlays (encrypted with ODDS_ALERTS_KEY)
Remembers what it already sent in alerted.json (kept on the odds-data branch), so each pick alerts once.
A pick alerts again only if its edge has grown by 3+ points or the price is up by 5c+ (marked "⬆️ Better price").

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
REALERT_PRICE = 0.05   # same pick alerts again only when the price is up this much (or the edge is up 3+ points)
MAX_PRICE = float(os.environ.get("MAX_PRICE", "2.0"))   # sports bets over this price never alert (racing exempt)
DISCORD = SITE + "/discord.html"
MIN_FORM = float(os.environ.get("MIN_FORM", "0.6"))   # player props: he must have landed this side in 60%+ of his last 15
MIN_FORM_GAMES = 3


def backed(form):
    """False when the player's own numbers go against the bet (price value alone isn't enough to alert)."""
    if not form or len(form) < 2 or (form[1] or 0) < MIN_FORM_GAMES:
        return True   # no stats to judge it on (game markets, players we don't have logs for)
    return form[0] / form[1] >= MIN_FORM


# Tighter bars for the markets the bet log showed leaking (first week: game H2H 8-23, Nations League 0-9,
# Pts + Reb 5-9, main-line Overs 17-18 while Unders and ladders made the money)
GAME_MIN = float(os.environ.get("GAME_MIN", "6.0"))     # game markets: Pinnacle-priced only, this much edge
COMBO_MIN = float(os.environ.get("COMBO_MIN", "6.0"))   # Pts + Reb / Pts + Ast props
OVER_FORM = float(os.environ.get("OVER_FORM", "0.7"))   # main-line Overs: this hit rate over his last 15...
OVER_MIN = float(os.environ.get("OVER_MIN", "6.0"))     # ...or this much edge
COMBO = {"player_points_rebounds", "player_points_assists"}


def refine(p, form=None, ladder=False):
    """Why this pick misses the tighter bars, or None when it can alert."""
    lg = p.get("log") or {}
    if not lg:
        return None   # racing
    mk = str(lg.get("mk") or "").replace("_alternate", "")
    if not lg.get("desc"):   # game market (H2H / line / total)
        if str(lg.get("sport_key") or "").startswith("soccer"):
            return "soccer game market"
        if lg.get("src") != "pin":
            return "game market without a Pinnacle price"
        if p["edge"] < GAME_MIN:
            return f"game market under {GAME_MIN:g}% edge"
        return None
    if mk in COMBO and p["edge"] < COMBO_MIN:
        return f"combo prop under {COMBO_MIN:g}% edge"
    if lg.get("side") == "Over" and not ladder and p["edge"] < OVER_MIN:
        ok = form and len(form) >= 2 and (form[1] or 0) >= MIN_FORM_GAMES and form[0] / form[1] >= OVER_FORM
        if not ok:
            return f"main-line Over under {OVER_FORM:.0%} form and {OVER_MIN:g}% edge"
    return None


MAX_PER_PUSH = 6
MAX_SINGLE = 10**6   # no limit: every new value bet gets its own notification
BANK_UNITS = 100     # stake suggestion = quarter Kelly on a 100-unit bank, 0.5U to 3U


GOOD_MATCHUP = ("matchup is on his side", "matchup is on the Under's side", "a good matchup for him", "which suits the Under")
LIKED_VERDICT = ("is one we like", "looks the right side of this line")


def founder_ok(p):
    """Founding members get the props where the write-up says the matchup is good AND the verdict is one we like."""
    secs = {}
    for para in ((p.get("why") or [""])[0] or "").split("\n\n"):
        k = para.split(":", 1)[0]
        for name in ("Matchup", "Verdict"):
            if k.endswith(name):
                secs[name] = para
    return any(x in secs.get("Matchup", "") for x in GOOD_MATCHUP) and any(x in secs.get("Verdict", "") for x in LIKED_VERDICT)


def alert_tier(p):
    """Who gets this alert in the app / Discord: founder (best-backed props) < core (all props) < vip (everything)."""
    prop = bool((p.get("log") or {}).get("desc")) and p.get("emoji") not in ("🏇", "🐕")
    return ("founder" if founder_ok(p) else "core") if prop else "vip"


def app_push(cat, title, lines, url, min_tier, full=None, thread=None, sent=None):
    """Members' app alerts (web push, scripts/web_push.py), next to the ntfy channels, plus the matching
    Discord channel (scripts/discord_post.py). Discord gets `full` (the whole write-up) when given, since
    push notifications are short. Never fails the caller."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import discord_post
        discord_post.alert(cat, title, full or lines, url, min_tier, thread=thread, sent=sent)
    except Exception as e:
        print(f"::warning::discord post failed: {e}")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import web_push
        return web_push.send(cat, title, lines, url, min_tier)
    except Exception as e:
        print(f"::warning::app push failed: {e}")
        return 0


def push(title, lines, click=None, actions=()):
    """Send to Jayden's topic and, if set, the VIP members' topic. True if any send worked."""
    ok = False
    for t in dict.fromkeys(x for x in (TOPIC, MEMBER_TOPIC) if x):   # same topic in both secrets = send once
        ok = _push(t, title, lines, click, actions) or ok
    if not TOPIC and not MEMBER_TOPIC:
        print("::warning::NTFY_TOPIC secret is empty - value alerts not sent")
    return ok


def _push(topic, title, lines, click=None, actions=(), priority=4):
    body = "\n".join(lines)
    acts = list(actions)[:2] + [{"action": "view", "label": "Open Discord", "url": DISCORD, "clear": False}]
    msg = {"topic": topic, "title": title, "message": body,   # titles carry their own emoji, so no ntfy tag (it doubled up)
           "priority": priority, "actions": acts}
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


def pstake(p):
    """Stake for an alert: half size (min 0.5U) when the player is questionable or game-time."""
    u = stake(p["edge"], p["price"])
    return max(0.5, round(u / 2 * 2) / 2) if "⚠️" in (p.get("tag") or "") else u


def tip(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(SYD).strftime("%a %-I:%M%p").replace("AM", "am").replace("PM", "pm")
    except Exception:
        return ""


START_BUFFER = timedelta(minutes=float(os.environ.get("START_BUFFER_MIN", "10")))   # no alerts this close to (or after) start


def started(iso, now):
    """True once the game is within START_BUFFER of starting: too late to get on, and the
    scheduled time can be off. Racing uses the same cutoff."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")) <= now + START_BUFFER
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
    """Short tipster-style case for an NBA/NBL prop: the write-up from hot.json (writeup.py), else quick bullets."""
    if v.get("why"):
        return [v["why"]]
    out, pt, sd = [], v.get("point"), side.lower()
    games = v.get("games") or []
    if games:
        out.append(f"Last 5: {', '.join(str(g[3]) for g in games[:5])}. Averaging {v.get('avg')} on a {pt} line")
    l10, l5 = v.get("l10"), v.get("l5")
    if l10 and l10[1]:
        bit = f"Gone {sd} in {l10[0]} of his last {l10[1]}"
        if l5 and l5[1]:
            bit += f", {l5[0]} of the last {l5[1]}"
        if v.get("vs") and v["vs"][1]:
            bit += f", {v['vs'][0]} of {v['vs'][1]} v {v.get('opp')}"
        out.append(bit)
    if v.get("ctx"):
        out.append(v["ctx"][:1].upper() + v["ctx"][1:])
    if v.get("wo"):
        w = v["wo"]
        last = w["n"].split()[-1]
        out.append(f"{last} out. He's averaging {w['avg']} without him, {w['with']} with")
    pr = sorted((v.get("prices") or {}).items(), key=lambda x: -x[1])
    if len(pr) > 1:
        out.append(f"Best price {pr[0][1]:.2f} {pr[0][0]}, most books {sorted(p for _, p in pr)[len(pr)//2]:.2f}")
    if v.get("inj"):
        out.append(f"He's listed {v['inj']}, make sure he plays")
    return out


def _nm(x):
    return " ".join(re.sub(r"[^a-z0-9 ]", "", str(x or "").lower().replace("-", " ")).split())


def team_map(hot):
    """{player name: team} from Hot stats (NBA + NBL) and the public NBL player list, so alerts can say who he plays for."""
    m = {}
    for p in (load(os.path.join(OUT, "nbl_players.json"), {}) or {}).get("players") or []:
        if p.get("n") and p.get("t"):
            m[_nm(p["n"])] = p["t"]
    for lst in ("items", "value"):
        for v in hot.get(lst) or []:
            if v.get("player") and v.get("team"):
                m[_nm(v["player"])] = v["team"]
    return m


def with_team(sel, teams):
    """'Dash Daniels Over 4.5' -> 'Dash Daniels (Melbourne United) Over 4.5' when we know his team."""
    w = str(sel).split()
    for k in range(min(len(w), 4), 0, -1):
        t = teams.get(_nm(" ".join(w[:k])))
        if t:
            return " ".join(w[:k]) + f" ({t})" + ("" if k == len(w) else " " + " ".join(w[k:]))
    return sel


def same_bet(p):
    """One key per real bet whatever source found it: player, market (main and alt lines together), side, line."""
    lg = p.get("log") or {}
    if not (lg.get("desc") and lg.get("mk") and lg.get("side") and lg.get("point") is not None):
        return None
    return f"bet|{_nm(lg['desc'])}|{str(lg['mk']).replace('_alternate', '')}|{lg['side']}|{float(lg['point']):g}"


def collect(now):
    out = []
    hot = load(os.path.join(OUT, "hot.json"), {})
    teams = team_map(hot)
    for v in hot.get("value") or []:
        if (v.get("value") or 0) < MIN_VALUE or started(v.get("start", ""), now) or not backed(v.get("l10")) \
                or (v.get("price") or 0) > MAX_PRICE:
            continue
        side = "Under" if str(v.get("line", "")).lower().startswith("u") else "Over"
        add = SITE + "/tracker.html?" + urllib.parse.urlencode({
            "add": 1, "lg": v.get("lg") or "NBA", "player": v["player"], "stat": v["stat"], "side": side,
            "line": v.get("point", ""), "odds": v["price"], "book": v["book"]})
        out.append({"tag": tag(v), "key": f"hot|{v['player']}|{v['stat']}|{v.get('point')}|{side}",
                    "edge": v["value"], "price": v["price"], "start": v.get("start", ""),
                    "text": f"{with_team(v['player'], teams)}{tag(v)} {v['line']} {v['stat']} @ {v['price']:.2f} {v['book']}",
                    "sel": f"{with_team(v['player'], teams)}{tag(v)} {v['line']} {v['stat']}", "book": v["book"],
                    "game": v.get("game", ""), "lg": v.get("lg", ""), "emoji": emoji(v.get("sk"), v.get("lg")),
                    "sub": f"fair {100*v.get('prob',0):.0f}%",
                    "why": analysis(v, side),
                    "add": add, "src_item": v,   # devil's advocate reads the model's numbers off this
                    "log": {"ev": v.get("ev"), "mk": v.get("mk"), "desc": v.get("desc") or v["player"], "side": side,
                            "point": v.get("point"), "sport_key": v.get("sk"), "fair": round(1 / v["prob"], 3) if v.get("prob") else None,
                            "src": "hot-" + str(v.get("msrc") or "")}})
        why_not = refine(out[-1], v.get("l10"), str(v.get("line", "")).endswith("+") or "_alternate" in str(v.get("mk")))
        if why_not:
            print(f"skipped ({why_not}): {out.pop()['sel']}")
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
            if o["edge_pct"] < MIN_VALUE or started(o.get("start", ""), now) or o["price"] > MAX_PRICE:
                continue
            if o.get("model") and not o["model"].get("agree"):
                print(f"skipped (Betfair's model disagrees): {o['pick']}")
                continue
            if not backed(o.get("form")):
                print(f"skipped (stats don't back it, {o['form'][0]}/{o['form'][1]}): {o['pick']}")
                continue
            out.append({"tag": tag(o), "key": f"ov|{o['game']}|{o['market']}|{o['pick']}",
                        "edge": o["edge_pct"], "price": o["price"], "start": o.get("start", ""),
                        "text": f"{with_team(o['pick'], teams)}{tag(o)} ({o['market']}) @ {o['price']:.2f} {o['book']}",
                        "sel": f"{with_team(o['pick'], teams)}{tag(o)} ({o['market']})", "book": o["book"],
                        "game": away_at_home(o["game"], o.get("sport_key")), "lg": o.get("sport", ""),
                        "emoji": emoji(o.get("sport_key"), o.get("sport")),
                        "sub": f"fair {o['fair']:.2f}",
                        "why": [o["why"]] if o.get("why") else [],
                        "add": None, "src_item": o,
                        "log": {"ev": o.get("ev"), "mk": o.get("mk"), "desc": o.get("desc") or "", "side": o.get("side"),
                                "point": o.get("point"), "sport_key": o.get("sport_key"), "fair": o.get("fair"),
                                "src": o.get("fair_src")}})
            why_not = refine(out[-1], o.get("form"), "_alternate" in str(o.get("mk")))
            if why_not:
                print(f"skipped ({why_not}): {out.pop()['sel']}")
        for o in summ.get("racing_value") or []:   # Betfair back price over the Hub model's rated price
            if started(o.get("start", ""), now):
                continue
            out.append({"tag": "", "key": f"race|{o['game']}|{o['pick']}", "edge": o["edge_pct"], "price": o["price"],
                        "start": o.get("start", ""), "text": f"{o['pick']} (Win) @ {o['price']:.2f} Betfair",
                        "sel": f"{o['pick']} (Win)", "book": "Betfair", "game": o["game"], "lg": o.get("sport", ""),
                        "emoji": "🏇" if o.get("sport") == "Racing" else "🐕", "sub": f"rated {o['fair']:.2f}",
                        "why": [o["why"]], "add": None, "log": None})
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
        app_push("arbs", a["title"], lines[:4], SITE + "/odds.html", "vip")
        if push(a["title"], lines, click=SITE + "/odds.html",
                actions=[{"action": "view", "label": "Odds tracker", "url": SITE + "/odds.html", "clear": False}]):
            state[a["key"]] = {"edge": a["margin"], "price": 0, "tag": "", "at": now.isoformat()}
            sent += 1
    print(f"arb alerts: {len(arbs)} at {ARB_MIN}%+, {sent} sent")
    return sent


RACE_AM_HOUR = int(os.environ.get("RACE_AM_HOUR", "9"))   # Sydney time
BOTD_HOUR = int(os.environ.get("BOTD_HOUR", "8"))         # Melbourne time: pick the bet of the day from 8am
MEL = ZoneInfo("Australia/Melbourne")


def bet_of_day(now, state):
    """One free pick a day: the best value bet starting in the next 20 hours that its stats (props) or Betfair's
    model (game bets) back, at a sensible price. Locked once picked, logged so it's graded like every alert,
    published as botd.json for the home page and pushed to every member + Jayden."""
    mel = now.astimezone(MEL)
    day = mel.date().isoformat()
    b = state.get("botd") or {}
    hist = b.get("hist") or []
    # results for past picks, from the public Tracker record the bet log writes
    rec = load(os.path.join(OUT, "record.json"), {}) or {}
    done = {(r.get("sel"), str(r.get("start"))[:16]): r.get("result") for r in rec.get("recent") or []}
    for h in hist:
        if not h.get("result"):
            h["result"] = done.get((h.get("sel"), str(h.get("start"))[:16]))
    if b.get("date") != day and mel.hour >= BOTD_HOUR:
        cands = []
        for p in collect(now):
            if not p.get("log") or not p.get("start") or not (1.55 <= p["price"] <= 3.2) or p["edge"] < 3:
                continue
            try:
                mins = (datetime.fromisoformat(p["start"].replace("Z", "+00:00")) - now).total_seconds() / 60
            except ValueError:
                continue
            if 45 <= mins <= 20 * 60:
                cands.append(p)
        # props with a full write-up first (stats already back them), then the biggest edge
        cands.sort(key=lambda p: (bool((p["log"] or {}).get("desc")) and bool(p.get("why")), p["edge"]), reverse=True)
        if cands:
            p = cands[0]
            pick = {"date": day, "sel": p["sel"], "game": p.get("game", ""), "lg": p.get("lg", ""), "start": p["start"],
                    "price": p["price"], "book": p["book"], "edge": p["edge"], "emoji": p["emoji"],
                    "why": (p.get("why") or [""])[0], "add": p.get("add")}
            hist = ([{k: pick[k] for k in ("date", "sel", "game", "lg", "start", "price", "book")}] + hist)[:30]
            b = {"at": now.isoformat(), "date": day, "pick": pick, "hist": hist}
            state["botd"] = b
            title = f"⭐ Bet of the day · {p['sel']}"
            lines = [f"{p['emoji']} {p['game']}{' (' + p['lg'] + ')' if p.get('lg') else ''} · {tip(p['start'])}",
                     f"${p['price']:.2f} at {p['book']}"]
            why = pick["why"]
            try:
                _push(TOPIC, title, lines + ([why] if why else []), click=SITE + "/#botd")
            except Exception as e:
                print(f"::warning::bet of the day ntfy failed: {e}")
            app_push("botd", title, lines + [x for x in why.split("\n\n") if x.startswith(("📈", "✅", "🤖"))][:2],
                     SITE + "/#botd", "founder", sent=(posted := []))
            try:
                import live_prices
                live_prices.add(p, posted)
            except Exception as e:
                print(f"::warning::live prices (bet of the day): {e}")
            try:   # graded by the bet log like every alert
                old = load(os.path.join(OUT, "new_alerts.json"), [])
                row = {**p["log"], "id": p["key"] + "|" + p["start"], "price": p["price"], "book": p["book"], "edge": p["edge"],
                       "stake": pstake(p), "sel": p["sel"], "game": p.get("game", ""), "lg": p.get("lg", ""),
                       "start": p["start"]}
                with open(os.path.join(OUT, "new_alerts.json"), "w") as f:
                    json.dump(old + [row], f)
            except Exception as e:
                print(f"::warning::couldn't log the bet of the day: {e}")
            print(f"bet of the day: {p['sel']} @ {p['price']}")
    elif b:
        b["hist"] = hist
        b["at"] = now.isoformat()   # keep it in the state file
    # public file for the home page: today's pick (once it's picked) + the record of past picks
    w = sum(1 for h in hist if h.get("result") == "win")
    l = sum(1 for h in hist if h.get("result") == "loss")
    pub = {"updated": now.isoformat(), "today": (b.get("pick") if b.get("date") == day else None),
           "record": [w, l], "hist": hist[:14]}
    with open(os.path.join(OUT, "botd.json"), "w") as f:
        json.dump(pub, f, separators=(",", ":"))


def race_morning(now, state):
    """Once a race day, from 9am Sydney: the Betfair models' standout picks (top-rated runner well clear of
    the next best), so there's a racing push even without live Betfair prices."""
    syd = now.astimezone(SYD)
    if syd.hour < RACE_AM_HOUR or syd.hour >= 18:
        return
    key = os.environ.get("ODDS_ALERTS_KEY", "").strip()
    bundle = load(os.path.join(OUT, "picks.json"), None)
    if not (key and bundle and bundle.get("ct")):
        return
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import picks
    tops = (picks.decrypt_with_key(bundle, key) or {}).get("race_tops") or {}
    day = syd.date().isoformat()
    if tops.get("date") != day or not tops.get("items") or (state.get("race_am") or {}).get("date") == day:
        return
    lines = []
    for kind, emo in (("Horse", "🏇"), ("Greyhound", "🐕")):
        xs = [x for x in tops["items"] if x["kind"] == kind]
        if not xs:
            continue
        lines += ["", f"{emo} {'Horses' if kind == 'Horse' else 'Greyhounds'}"]
        for x in xs:
            lines.append(f"{x['venue']} R{x['num']}: {(str(x['no']) + '. ') if x.get('no') else ''}{x['name']} rated ${x['rated']:.2f} "
                         f"(next best {x['next']} ${x['next_rated']:.2f})")
    lines += ["", "Rated prices from Betfair's data scientists' models. Only back them if your bookie pays more than the rated price."]
    title = f"🏇 Model's top picks · {syd.strftime('%a %-d %b')}"
    _push(TOPIC, title, lines[1:], click=SITE + "/odds.html", priority=3)
    app_push("racing", title, [l for l in lines[1:] if l][:6], SITE + "/odds.html", "founder")   # free for every member
    state["race_am"] = {"at": now.isoformat(), "date": day}
    print(f"racing morning push: {len(tops['items'])} picks")


FOUNDER_MIN = int(os.environ.get("FOUNDER_MIN", "2"))            # founders get at least this many props a day
FOUNDER_FLOOR_HOUR = int(os.environ.get("FOUNDER_FLOOR_HOUR", "10"))   # Melbourne: top up from 10am if short


def founder_day(state, now):
    """Today's (Melbourne) founder-pick count, kept in the state file."""
    day = now.astimezone(MEL).date().isoformat()
    f = state.get("founder_day") or {}
    if f.get("date") != day:
        f = {"date": day, "n": 0, "keys": []}
    f["at"] = now.isoformat()
    state["founder_day"] = f
    return f


def founder_floor(now, state, mute):
    """If fewer than FOUNDER_MIN props have gone to founders today by FOUNDER_FLOOR_HOUR, send the day's best
    remaining props to #founder-picks so the channel is never empty. Best = the write-up's lean first
    (liked verdict, good matchup, any lean), then edge. Logged as founder picks so the tracker counts them."""
    f = founder_day(state, now)
    if f["n"] >= FOUNDER_MIN or now.astimezone(MEL).hour < FOUNDER_FLOOR_HOUR:
        return
    cands = []
    for p in collect(now):
        lg = p.get("log") or {}
        if not lg.get("desc") or p.get("emoji") in ("🏇", "🐕") or not p.get("start") or not (1.5 <= p["price"] <= 3.5):
            continue
        if p["key"] in f["keys"] or founder_ok(p):   # a founder_ok bet goes out through the normal alert
            continue
        if mute and lg.get("sport_key"):
            import bet_log
            b = {"lg": p.get("lg"), "sport_key": lg["sport_key"], "desc": lg["desc"], "mk": lg.get("mk")}
            if bet_log.bucket(b) in mute or bet_log.mbucket(b) in mute:
                continue
        try:
            mins = (datetime.fromisoformat(p["start"].replace("Z", "+00:00")) - now).total_seconds() / 60
        except ValueError:
            continue
        if not 30 <= mins <= 20 * 60:
            continue
        w = (p.get("why") or [""])[0]
        lean = (any(x in w for x in LIKED_VERDICT), any(x in w for x in GOOD_MATCHUP), "On balance it leans" in w)
        if "doesn't back it up" in w or "enough warning signs" in w:
            continue
        cands.append((lean, p["edge"], p))
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    rows = []
    for _, _, p in cands[:FOUNDER_MIN - f["n"]]:
        title = f"🔒 Founder pick · +{p['edge']:.1f}% · {p['sel']}"
        lines = [f"{p['emoji']} {p['game']}{' (' + p['lg'] + ')' if p.get('lg') else ''} · {tip(p['start'])}",
                 f"${p['price']:.2f} at {p['book']} · {pstake(p):g}U · {p['sub']}"] + \
                [w for w in (p.get("why") or [""])[0].split("\n\n") if w.strip()]
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import discord_post
            if not discord_post.send("founder-picks", title, lines, p.get("add") or SITE + "/odds.html"):
                continue
        except Exception as e:
            print(f"::warning::founder top-up post failed: {e}")
            continue
        f["n"] += 1
        f["keys"].append(p["key"])
        rows.append({**p["log"], "id": p["key"] + "|" + p["start"], "price": p["price"], "book": p["book"], "edge": p["edge"],
                     "stake": pstake(p), "sel": p["sel"], "game": p.get("game", ""), "lg": p.get("lg", ""),
                     "start": p["start"], "tier": "founder"})
        print(f"founder top-up: {p['sel']} (+{p['edge']:.1f}%)")
    if rows:
        try:   # graded by the bet log; an already-logged bet just gets upgraded to a founder pick
            old = load(os.path.join(OUT, "new_alerts.json"), [])
            with open(os.path.join(OUT, "new_alerts.json"), "w") as fh:
                json.dump(old + rows, fh)
        except Exception as e:
            print(f"::warning::couldn't log the founder top-up: {e}")
    elif f["n"] < FOUNDER_MIN:
        print(f"founder top-up: {f['n']}/{FOUNDER_MIN} today, nothing suitable yet")


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

    try:   # league + market types the bet log found don't beat the closing price
        import bet_log
        mute = bet_log.muted()
    except Exception as e:
        print(f"::warning::couldn't read mutes: {e}")
        mute = set()
    try:
        bet_of_day(now, state)
    except Exception as e:
        print(f"::warning::bet of the day failed: {e}")
    try:
        race_morning(now, state)
    except Exception as e:
        print(f"::warning::racing morning push failed: {e}")
    new, seen = [], set()
    for p in sorted(collect(now), key=lambda x: -x["edge"]):
        # the same bet can come from two sources (Hot stats value + the odds overlays) under different names
        # ("8+ Pts + Reb" vs "Over 7.5 (Points Rebounds)"): one identity for both, best edge wins
        ck = same_bet(p)
        if ck:
            if ck in seen:
                continue
            seen.add(ck)
        if mute and (p.get("log") or {}).get("sport_key"):
            b = {"lg": p.get("lg"), "sport_key": p["log"]["sport_key"], "desc": p["log"]["desc"], "mk": p["log"].get("mk")}
            if bet_log.bucket(b) in mute or bet_log.mbucket(b) in mute:
                print(f"skipped (muted market): {p['sel']}")
                continue
        old = state.get(p["key"]) or (state.get(ck) if ck else None)
        cleared = "⚠️" in old.get("tag", "") if old else False   # was questionable, now isn't
        if old and p["edge"] < old["edge"] + 3 and p["price"] < old["price"] + REALERT_PRICE and not (cleared and "⚠️" not in p["tag"]):
            continue
        p["again"] = bool(old)
        p["ck"] = ck
        new.append(p)
    if not new:
        print("value alerts: nothing new")
    else:
        # one notification per bet, so each has its own Open Discord and Log bet buttons
        top = new[:MAX_SINGLE]
        sent = []
        for p in top:
            if p.get("start") and started(p["start"], datetime.now(timezone.utc)):   # sending takes a while: check the clock again
                print(f"skipped (starting): {p['sel']}")
                continue
            lg = f" ({p['lg']})" if p.get("lg") else ""
            title = ("⬆️ Better price · " if p.get("again") else "💰 ") + f"+{p['edge']:.1f}% · {p['sel']}"
            lines = [f"{p['emoji']} {p['game']}{lg} · {tip(p['start'])}",
                     f"${p['price']:.2f} at {p['book']} · {pstake(p):g}U · {p['sub']}"] + p.get("why", [])
            acts = [{"action": "view", "label": "Log bet", "url": p["add"], "clear": False}] if p["add"] else []
            acts.append({"action": "view", "label": "Odds tracker", "url": SITE + "/odds.html", "clear": False})
            if push(title, lines, click=SITE + "/odds.html", actions=acts):
                sent.append(p)
            # app alerts: props by league to Daily card + VIP; game bets and racing value to VIP
            prop = bool((p.get("log") or {}).get("desc"))
            cat = "racing" if p["emoji"] in ("🏇", "🐕") else (str(p.get("lg") or "").lower() if prop else "games")
            if cat not in ("nba", "nbl", "nfl", "racing", "games"):
                cat = "games"
            why = (p.get("why") or [""])[0].split("\n\n")
            th = None
            if p.get("log") and p["key"].startswith(("hot|", "ov|")):   # devil's advocate: the case against, as a thread
                try:
                    import devils_advocate as da
                    bk = None
                    if (p["log"] or {}).get("sport_key"):
                        import bet_log
                        bk = bet_log.bucket({"lg": p.get("lg"), "sport_key": p["log"]["sport_key"], "desc": p["log"].get("desc")}).replace("|", " ")
                    p["da"] = da.case(p, load(os.path.join(OUT, "record.json"), {}), bk)
                    ai = da.gemini(p, p["da"])
                    if ai:
                        p["da"]["ai"] = ai
                        print(f"devil's advocate (Gemini): {ai['level']} vs {p['sel']}")
                    th = da.thread(p["da"], p["sel"])
                    print(f"devil's advocate: {p['da']['level']} ({p['da']['score']}) vs {p['sel']}")
                except Exception as e:
                    print(f"::warning::devil's advocate failed: {e}")
            app_push(cat, title, lines[:2] + [w for w in why if w.startswith(("📈", "✅", "🤖"))][:2] or lines[:3],
                     (p.get("add") or SITE + "/odds.html"), alert_tier(p),
                     full=lines[:2] + [w for w in why if w.strip()], thread=th, sent=(posted := []))
            try:   # live prices panel on those Discord messages (live_prices.py edits it every odds update)
                import live_prices
                live_prices.add(p, posted)
            except Exception as e:
                print(f"::warning::live prices: couldn't track {p['sel']}: {e}")   # Discord: every section (form, matchup, role, game, price, verdict)
        if len(new) > len(top):   # the rest in one summary
            rest = new[len(top):]
            lines = [f"+{p['edge']:.1f}% {p['text']} · {pstake(p):g}U" for p in rest[:10]]
            if len(rest) > 10:
                lines.append(f"+{len(rest) - 10} more on the odds tracker")
            if push(f"💰 {len(rest)} more value bets", lines, click=SITE + "/odds.html",
                    actions=[{"action": "view", "label": "Odds tracker", "url": SITE + "/odds.html", "clear": False}]):
                sent += rest
        fd = founder_day(state, now)
        for p in sent:
            if alert_tier(p) == "founder" and p["key"] not in fd["keys"]:
                fd["n"] += 1
                fd["keys"].append(p["key"])
        for p in sent:
            state[p["key"]] = {"edge": p["edge"], "price": p["price"], "tag": p["tag"], "at": now.isoformat()}
            if p.get("ck"):
                state[p["ck"]] = state[p["key"]]
        # hand every sent bet to the bet log (bet_log.py scores it on closing price + result)
        rows = [{**p["log"], "id": p["key"] + "|" + p.get("start", ""), "price": p["price"], "book": p["book"],
                 "edge": p["edge"], "stake": pstake(p), "sel": p["sel"], "game": p.get("game", ""),
                 "lg": p.get("lg", ""), "start": p.get("start", ""),
                 # who got it in the app: props go to Daily card + VIP, game bets and racing to VIP only
                 "tier": alert_tier(p),
                 # devil's advocate's case against (flag only), so the bet log can show if it's worth listening to
                 **({"da": p["da"]["score"]} if p.get("da") else {}),
                 **({"dai": p["da"]["ai"]["level"]} if (p.get("da") or {}).get("ai") else {})}
                for p in sent if p.get("log") and p.get("start")]
        try:
            old = load(os.path.join(OUT, "new_alerts.json"), [])
            with open(os.path.join(OUT, "new_alerts.json"), "w") as f:
                json.dump(old + rows, f)
        except Exception as e:
            print(f"::warning::couldn't hand alerts to the bet log: {e}")
    try:
        founder_floor(now, state, mute)
    except Exception as e:
        print(f"::warning::founder top-up failed: {e}")
    arb_sent = 0
    try:
        arb_sent = send_arbs(state, now) or 0
    except Exception as e:
        print(f"::warning::arb alerts failed: {e}")
    # every update: a short status ping to Jayden's own channel only (UPDATE_PING=1), even when nothing is new
    # only when something changed: new value bets or new arbs (no repeat "nothing new" pings)
    if os.environ.get("UPDATE_PING") == "1" and TOPIC and (new or arb_sent):
        allv = sorted(collect(now), key=lambda x: -x["edge"])
        when = datetime.now(SYD).strftime("%-I:%M%p").lower()
        lines = [f"{len(allv)} value bets live · {len(new)} new this update" + (f" · {arb_sent} new arbs" if arb_sent else ""),
                 f"Best Betfair arb right now: {BEST_ARB:.2f}%" if BEST_ARB is not None else "No Betfair arbs right now"]
        if allv:
            lines.append("Top value:")
            for p in allv[:3]:
                lines.append(f"+{p['edge']:.1f}% {p['text']} · {pstake(p):g}U")   # one line each: the write-ups went out in the bet's own alert
        try:
            _push(TOPIC, f"📊 Odds updated {when}", lines, click=SITE + "/odds.html", priority=3)
        except Exception as e:
            print(f"::warning::update ping failed: {e}")
    with open(state_path, "w") as f:
        json.dump(state, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
