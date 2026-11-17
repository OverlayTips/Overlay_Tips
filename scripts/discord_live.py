"""Live bet tracking in Discord: #live-tracker gets one message per NBA game, edited every minute with each
alerted prop's progress (e.g. "Zion 18/22 pts · needs 4"), plus a cashed / missed / void post per bet.

Bets come from the bet log (every value alert the tracker sent), live numbers from BALLDONTLIE box scores.
No state file: the bot finds its own messages again by a small marker on the last line, so reruns never
double-post. Run in a loop by .github/workflows/discord-live.yml during NBA game hours.

Env: NBA_STATS_KEY (BALLDONTLIE), ODDS_ALERTS_KEY (decrypts the bet log), DISCORD_BOT_TOKEN, DISCORD_GUILD_ID,
     OUT (folder holding bet_log.json, default odds-data), ONCE=1 to run one pass and stop.
"""
import hashlib, json, math, os, re, subprocess, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import discord_post

BDL = "https://api.balldontlie.io/v1"
KEY = os.environ.get("NBA_STATS_KEY", "").strip()
LOG_KEY = os.environ.get("ODDS_ALERTS_KEY", "").strip()
OUT = os.environ.get("OUT", "odds-data")
CHANNEL = "live-tracker"
ET = ZoneInfo("America/New_York")
FOOT = "-# 18+ · Gamble responsibly · 1800 858 858"

# market key -> (short label, how to read it off a box-score row)
STAT = {
    "player_points": ("pts", lambda p: p["pts"]),
    "player_rebounds": ("reb", lambda p: p["reb"]),
    "player_assists": ("ast", lambda p: p["ast"]),
    "player_threes": ("3PM", lambda p: p["fg3m"]),
    "player_blocks": ("blk", lambda p: p["blk"]),
    "player_steals": ("stl", lambda p: p["stl"]),
    "player_points_rebounds_assists": ("PRA", lambda p: p["pts"] + p["reb"] + p["ast"]),
    "player_points_rebounds": ("P+R", lambda p: p["pts"] + p["reb"]),
    "player_points_assists": ("P+A", lambda p: p["pts"] + p["ast"]),
    "player_rebounds_assists": ("R+A", lambda p: p["reb"] + p["ast"]),
}


def _nm(x):
    x = unicodedata.normalize("NFKD", str(x or "")).encode("ascii", "ignore").decode()   # Traoré -> Traore
    x = re.sub(r"[^a-z ]", "", str(x or "").lower().replace("-", " ").replace(".", ""))
    return " ".join(w for w in x.split() if w not in ("jr", "sr", "ii", "iii", "iv"))


def bdl(path, params=()):
    qs = urllib.parse.urlencode(list(params))
    req = urllib.request.Request(f"{BDL}{path}" + ("?" + qs if qs else ""), headers={"Authorization": KEY})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("data") or []


def boxes():
    """Every NBA box score for the last two US dates plus anything live, keyed date|home id (live wins)."""
    today = datetime.now(ET).date()
    out = {}
    rows = []
    for d in (today - timedelta(days=1), today):
        try:
            rows += bdl("/box_scores", [("date", d.isoformat())])
        except Exception as e:
            print(f"::warning::box scores {d}: {e}")
    try:
        rows += bdl("/box_scores/live")
    except Exception as e:
        print(f"::warning::live box scores: {e}")
    for b in rows:
        out[f"{str(b.get('date', ''))[:10]}|{(b.get('home_team') or {}).get('id')}"] = b
    return out


def players(box):
    """{normalised name: player row} for both teams of one box score."""
    m = {}
    for side in ("home_team", "visitor_team"):
        for p in (box.get(side) or {}).get("players") or []:
            pl = p.get("player") or {}
            row = {k: p.get(k) or 0 for k in ("pts", "reb", "ast", "fg3m", "blk", "stl")}
            row["min"] = str(p.get("min") or "0")
            row["team"] = ((box.get(side) or {}).get("abbreviation") or "").upper()
            m[_nm(f"{pl.get('first_name', '')} {pl.get('last_name', '')}")] = row
    return m


def find(nm, sel, rows):
    """His box-score row: exact name, else same surname on the team in the bet label (Alex vs Alexandre Sarr)."""
    if nm in rows:
        return rows[nm]
    team = re.search(r"\(([A-Z]{2,4})\)", sel or "")
    last = nm.split()[-1] if nm else ""
    hits = [r for n, r in rows.items() if n.split() and n.split()[-1] == last and (not team or r["team"] == team.group(1))]
    return hits[0] if len(hits) == 1 else None


def refresh_log():
    """Pull the latest bet log so alerts sent after the run started get tracked too."""
    try:
        subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", "odds-data"], check=True, timeout=60)
        data = subprocess.run(["git", "show", "FETCH_HEAD:bet_log.json"], check=True, capture_output=True, timeout=30).stdout
        with open(os.path.join(OUT, "bet_log.json"), "wb") as f:
            f.write(data)
    except Exception as e:
        print(f"::warning::bet log refresh: {e}")


def game_state(box):
    s = str(box.get("status") or "")
    if s.lower().startswith("final"):
        return "final", "Final"
    if not box.get("period") or re.match(r"^\d{4}-\d{2}-\d{2}T", s) or re.search(r"\b(am|pm|ET)\b", s, re.I):
        return "pre", "Not started"
    p = box["period"]
    per = f"Q{p}" if p <= 4 else ("OT" if p == 5 else f"{p - 4}OT")
    t = str(box.get("time") or "").strip()
    return "live", (f"{per} {t}" if t and t.lower() not in ("final", "half") else (t.title() or per))


def load_bets(now):
    """NBA player-prop alerts that started up to 6 hours ago or start in the next 30 min, one per selection."""
    if not LOG_KEY:
        sys.exit("ODDS_ALERTS_KEY not set")
    import picks
    with open(os.path.join(OUT, "bet_log.json")) as f:
        log = picks.decrypt_with_key(json.load(f), LOG_KEY)
    seen, out = set(), []
    for b in sorted(log.get("bets", []), key=lambda b: -(b.get("edge") or 0)):
        if b.get("sport_key") != "basketball_nba" or b.get("mk") not in STAT or b.get("point") is None or not b.get("desc"):
            continue
        try:
            st = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
        except Exception:
            continue
        if not (now - timedelta(hours=6) <= st <= now + timedelta(minutes=30)):
            continue
        k = (_nm(b["desc"]), b["mk"], b["side"], b["point"])
        if k in seen:
            continue
        seen.add(k)
        out.append(b)
    return out


def progress(b, row, state):
    """(icon, text, result or None) for one bet given his box-score row."""
    lbl, f = STAT[b["mk"]]
    v, pt, over = f(row), float(b["point"]), b["side"] == "Over"
    need = math.floor(pt) + 1 if over else math.ceil(pt) - 1   # 22+ on a 21.5 line; Under 4.5 = 4 or fewer
    played = row["min"] not in ("", "0", "00", "0:00", "00:00")
    if over and v > pt:
        return "✅", f"{v} {lbl} · cashed", "win"
    if not over and v > pt:
        return "❌", f"{v} {lbl} · busted", "loss"
    if state == "final":
        if not played:
            return "↩️", "didn't play · void", "void"
        return ("❌", f"{v} {lbl} · missed by {need - v}", "loss") if over else ("✅", f"{v} {lbl} · cashed", "win")
    if state == "pre":
        return "⏳", "not started", None
    if over:
        return "🔥" if need - v <= 2 else "⏳", f"{v}/{need} {lbl} · needs {need - v}", None
    return "🛡️", f"{v} {lbl} · max {need}, room for {need - v}", None


class Live:
    def __init__(self):
        self.cid = discord_post._channel(CHANNEL)
        if not self.cid:
            sys.exit(f"no #{CHANNEL} channel: run the Discord setup workflow first")
        self.msgs, self.done = {}, set()
        for m in discord_post._call("GET", f"/channels/{self.cid}/messages?limit=100") or []:
            if not (m.get("author") or {}).get("bot"):
                continue
            tag = re.search(r"`(live|res):([^`]+)`", m.get("content") or "")
            if tag and tag.group(1) == "live":
                self.msgs.setdefault(tag.group(2), (m["id"], m["content"]))
            elif tag:
                self.done.add(tag.group(2))

    def upsert(self, key, text):
        old = self.msgs.get(key)
        if old and old[1] == text:
            return
        if old:
            discord_post._call("PATCH", f"/channels/{self.cid}/messages/{old[0]}", {"content": text})
        else:
            m = discord_post._call("POST", f"/channels/{self.cid}/messages",
                                   {"content": text, "allowed_mentions": {"parse": []}})
            old = (m["id"], None)
        self.msgs[key] = (old[0], text)

    def result(self, b, res, detail, game):
        rid = hashlib.sha1(str(b.get("id") or b["sel"]).encode()).hexdigest()[:10]
        if rid in self.done:
            return
        head = {"win": "✅ CASHED", "loss": "❌ MISSED", "void": "↩️ VOID"}[res]
        discord_post._call("POST", f"/channels/{self.cid}/messages", {"content":
            f"**{head} · {b['sel']}**\n{detail} · {game}\n-# `res:{rid}` · {FOOT[3:]}", "allowed_mentions": {"parse": []}})
        self.done.add(rid)
        print(f"result: {res} {b['sel']}")


def bp_started(b):
    return datetime.fromisoformat(b["start"].replace("Z", "+00:00")) < datetime.now(timezone.utc) - timedelta(minutes=15)


def one_pass(live, bets):
    """Update every game once. Returns True while anything is still to come or in play."""
    bx = boxes()
    where = {}   # box key -> (box, {name: row})
    for k, b in bx.items():
        where[k] = (b, players(b))
    games, pending = {}, False
    for b in bets:
        nm = _nm(b["desc"])
        day = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(ET).date().isoformat()
        hit = next(((k, bp, r) for k, bp in where.items() if k.startswith(day + "|")
                    for r in [find(nm, b["sel"], bp[1])] if r), None)   # that night's game, not a back-to-back
        if not hit:
            if bp_started(b):
                print(f"::notice::no box-score match for {b['sel']}")
            pending = pending or datetime.fromisoformat(b["start"].replace("Z", "+00:00")) > datetime.now(timezone.utc) - timedelta(hours=4)
            continue
        k, (box, rows), row = hit
        state, clock = game_state(box)
        icon, text, res = progress(b, row, state)
        games.setdefault(k, (box, state, clock, []))[3].append(f"{icon} **{b['sel']}** · {text}")
        h, a = box.get("home_team") or {}, box.get("visitor_team") or {}
        g = f"{a.get('abbreviation')} @ {h.get('abbreviation')}"
        if res:
            live.result(b, res, text.split(" · ")[0] if res != "void" else "Didn't play", g)
        if state != "final":
            pending = True
    for k, (box, state, clock, lines) in games.items():
        h, a = box.get("home_team") or {}, box.get("visitor_team") or {}
        score = "" if state == "pre" else f" · {a.get('abbreviation')} {box.get('visitor_team_score', 0)}-{box.get('home_team_score', 0)} {h.get('abbreviation')}"
        tag = f"{str(box.get('date', ''))[:10]}-{a.get('abbreviation')}-{h.get('abbreviation')}"
        note = "final" if state == "final" else "updates every minute"
        text = (f"🏀 **{a.get('full_name', a.get('abbreviation'))} @ {h.get('full_name', h.get('abbreviation'))}** · {clock}{score}\n"
                + "\n".join(lines) + f"\n-# Live tracker · {note} · `live:{tag}`")
        live.upsert(tag, text[:1990])
    return pending


def main():
    if not KEY:
        sys.exit("NBA_STATS_KEY not set")
    if not discord_post.enabled():
        sys.exit("DISCORD_BOT_TOKEN / DISCORD_GUILD_ID not set")
    bets = load_bets(datetime.now(timezone.utc))
    print(f"{len(bets)} NBA prop alerts in the window")
    if not bets:
        return
    live = Live()
    end = time.time() + float(os.environ.get("MINUTES", "28")) * 60
    n = 0
    while True:
        if n and n % 5 == 0:   # every 5 min: newly sent alerts and games tipping off later in the run
            refresh_log()
            bets = load_bets(datetime.now(timezone.utc))
        n += 1
        try:
            pending = one_pass(live, bets)
        except Exception as e:
            print(f"::warning::live pass failed: {type(e).__name__} {e}")
            pending = True
        if os.environ.get("ONCE") == "1" or not pending or time.time() + 60 > end:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
