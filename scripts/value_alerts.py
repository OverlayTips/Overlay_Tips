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
SYD = ZoneInfo("Australia/Sydney")
SITE = "https://overlay.tips"
FOOT = "Overlay Tips | 18+ Gamble responsibly"
MIN_VALUE = 4.0      # % edge needed to alert
MAX_PER_PUSH = 6
BANK_UNITS = 100     # stake suggestion = quarter Kelly on a 100-unit bank, 0.5U to 3U


def push(title, lines, click=None, actions=()):
    if not TOPIC:
        print("::warning::NTFY_TOPIC secret is empty - value alerts not sent")
        return False
    body = "\n".join(lines)
    wa = f"{title}\n\n{body}\n\n{FOOT}"
    acts = list(actions)[:2] + [{"action": "view", "label": "Share to WhatsApp",
                                 "url": "https://wa.me/?text=" + urllib.parse.quote(wa[:3500]), "clear": False}]
    msg = {"topic": TOPIC, "title": title, "message": body, "tags": ["moneybag"], "priority": 4, "actions": acts}
    if click:
        msg["click"] = click
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(msg).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        print(f"pushed: {title}")
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
                    "game": v.get("game", ""), "lg": v.get("lg", ""),
                    "sub": f"fair {100*v.get('prob',0):.0f}%"
                           + (f" · {v['wo']['n']} out: avg {v['wo']['avg']} w/o vs {v['wo']['with']} with" if v.get("wo") else ""),
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
                        "game": o["game"], "lg": o.get("sport", ""),
                        "sub": f"fair {o['fair']:.2f}",
                        "add": None})
    return out


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
        top = new[:MAX_PER_PUSH]
        lines = []
        # grouped by game, soonest first: "🏀 Utah Jazz @ Houston Rockets (NBA) · Sat 12:40pm" then its bets
        games = {}
        for p in top:
            games.setdefault((p["start"], p["game"]), []).append(p)
        for (start, game), ps in sorted(games.items()):
            if lines:
                lines.append("")
            lg = f" ({ps[0]['lg']})" if ps[0].get("lg") else ""
            lines.append(f"🏀 {game}{lg} · {tip(start)}")
            for p in ps:
                lines.append(f"+{p['edge']:.1f}% {p['text']} · {stake(p['edge'], p['price']):g}U · {p['sub']}")
        if len(new) > len(top):
            lines.append(f"+{len(new) - len(top)} more on the odds tracker")
        title = (f"💰 {len(new)} new value bets" if len(new) > 1
                 else f"💰 +{top[0]['edge']:.1f}% value · {top[0]['game']}")
        acts = [{"action": "view", "label": "Odds tracker", "url": SITE + "/odds.html", "clear": False}]
        if top[0]["add"]:
            acts.append({"action": "view", "label": "Log top bet", "url": top[0]["add"], "clear": False})
        if push(title, lines, click=SITE + "/odds.html", actions=acts):
            for p in new:
                state[p["key"]] = {"edge": p["edge"], "price": p["price"], "tag": p["tag"], "at": now.isoformat()}
    with open(state_path, "w") as f:
        json.dump(state, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
