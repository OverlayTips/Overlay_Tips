"""Live bet tracking in Discord: #live-tracker gets one message per NBA game, edited every minute with each
alerted prop's progress (e.g. "Zion 18/22 pts · needs 4"), plus one pinned "today's tally" message
(wins / losses / voids for the day) that's edited as each bet settles, instead of a post per bet.
#founder-tracker (FOUNDERS category) does the same for just the founder picks (the bets in #founder-picks),
with its own tally, off the same box scores. The bet of the day (botd.json) is tracked in both, tagged 🎯 BOTD.

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
FOUNDER_CHANNEL = "founder-tracker"
ET = ZoneInfo("America/New_York")
MEL = ZoneInfo("Australia/Melbourne")
ICON = {"win": "✅", "loss": "❌", "void": "↩️"}
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
    first = nm[:1]
    # same surname AND same first initial (Jalen Johnson must never match Keshad Johnson)
    hits = [r for n, r in rows.items() if n.split() and n.split()[-1] == last and n[:1] == first
            and (not team or r["team"] == team.group(1))]
    return hits[0] if len(hits) == 1 else None


def in_game(b, box):
    """Is this box score the bet's game? The bet stores "Away Team @ Home Team"; match on team nicknames
    (Hawks, Trail Blazers) so a player is never tracked in someone else's game. No game on the bet -> any."""
    g = (b.get("game") or "").lower()
    if not g:
        return True
    nick = [((box.get(s) or {}).get("name") or "").lower() for s in ("home_team", "visitor_team")]
    return any(n and n in g for n in nick)


def refresh_log():
    """Pull the latest bet log so alerts sent after the run started get tracked too."""
    try:
        subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", "odds-data"], check=True, timeout=60)
        data = subprocess.run(["git", "show", "FETCH_HEAD:bet_log.json"], check=True, capture_output=True, timeout=30).stdout
        with open(os.path.join(OUT, "bet_log.json"), "wb") as f:
            f.write(data)
        data = subprocess.run(["git", "show", "FETCH_HEAD:botd.json"], check=True, capture_output=True, timeout=30).stdout
        with open(os.path.join(OUT, "botd.json"), "wb") as f:
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
    if t.lower() == "final":
        return "final", "Final"
    hs, vs = box.get("home_team_score") or 0, box.get("visitor_team_score") or 0
    clock = re.sub(r"^(Q\d|\d?OT)\s*", "", t, flags=re.I)   # BDL often sends "Q4 :00.0"
    if p >= 4 and hs != vs and clock and re.fullmatch(r"[0:.\s]+", clock):
        return "final", "Final"   # clock run out in Q4/OT and not tied: BDL's status lags a few minutes behind
    if t.lower() in ("half", ""):
        return "live", t.title() or per
    return "live", f"{per} {clock}" if clock else per


def load_bets(now):
    """NBA player-prop alerts that started up to 6 hours ago or start in the next 30 min, one per selection.
    Returns (bets in the window, every deduped alert). A selection sent more than once counts as a founder
    pick if any of its alerts went to founders (tier "founder")."""
    if not LOG_KEY:
        sys.exit("ODDS_ALERTS_KEY not set")
    import picks
    with open(os.path.join(OUT, "bet_log.json")) as f:
        log = picks.decrypt_with_key(json.load(f), LOG_KEY)
    try:   # bet of the day: (selection, start to the minute), like bet_log.py
        with open(os.path.join(OUT, "botd.json")) as f:
            bj = json.load(f)
        botd = {(h.get("sel"), str(h.get("start"))[:16]) for h in ([bj.get("today")] if bj.get("today") else []) + (bj.get("hist") or [])}
    except Exception:
        botd = set()
    seen, every = {}, []
    for b in sorted(log.get("bets", []), key=lambda b: -(b.get("edge") or 0)):
        if b.get("sport_key") != "basketball_nba" or b.get("mk") not in STAT or b.get("point") is None or not b.get("desc"):
            continue
        try:
            st = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
        except Exception:
            continue
        k = (_nm(b["desc"]), b["mk"], b["side"], b["point"])
        if k in seen:
            if b.get("tier") == "founder":
                seen[k]["tier"] = "founder"
            if (b.get("sel"), str(b.get("start"))[:16]) in botd:
                seen[k]["botd"] = True
            continue
        seen[k] = b = dict(b)
        if (b.get("sel"), str(b.get("start"))[:16]) in botd:
            b["botd"] = True
        every.append(b)
    out = [b for b in every if now - timedelta(hours=6) <= datetime.fromisoformat(b["start"].replace("Z", "+00:00")) <= now + timedelta(minutes=30)]
    return out, every


def is_founder(b):
    """Founders' channel: their picks plus the bet of the day."""
    return b.get("tier") == "founder" or bool(b.get("botd"))


def name(b):
    return ("🎯 BOTD · " if b.get("botd") else "") + f"**{b['sel']}**"


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
        # 🔥 = close: at least halfway and within a quarter of the target (2 for small lines), never at 0
        hot = v > 0 and v * 2 >= need and need - v <= max(2, math.ceil(need / 4))
        return "🔥" if hot else "⏳", f"{v}/{need} {lbl} · needs {need - v}", None
    return "🛡️", f"{v} {lbl} · max {need}, room for {need - v}", None


class Live:
    def __init__(self, channel=CHANNEL, pick=None, label="Live tracker"):
        self.channel, self.pick, self.label = channel, pick or (lambda b: True), label
        self.day_sels = {}   # Melbourne day -> {selection: stake} for every bet this channel tracks that day ("bets left")
        self.cid = discord_post._channel(channel)
        if not self.cid:
            raise LookupError(f"no #{channel} channel: run the Discord setup workflow first")
        self.msgs = {}
        msgs = discord_post._call("GET", f"/channels/{self.cid}/messages?limit=100") or []
        try:
            msgs += discord_post._call("GET", f"/channels/{self.cid}/pins") or []   # the tally, even if it's scrolled away
        except Exception:
            pass
        extra, ids = [], set()
        for m in msgs:   # newest first
            if not (m.get("author") or {}).get("bot") or m["id"] in ids:
                continue
            ids.add(m["id"])
            if m.get("type") == 6:   # "pinned a message" notices from the bot's own pins
                extra.append(m["id"])
                continue
            tag = re.search(r"`live:([^`]+)`", m.get("content") or "")
            if tag:
                if tag.group(1) in self.msgs:   # a double post (two runs overlapping): keep the newest
                    extra.append(m["id"])
                else:
                    self.msgs[tag.group(1)] = (m["id"], m["content"])
        for mid in extra:
            try:
                discord_post._call("DELETE", f"/channels/{self.cid}/messages/{mid}")
            except Exception as e:
                print(f"::notice::#{channel}: couldn't delete {mid}: {e}")
        if extra:
            print(f"#{channel}: cleaned up {len(extra)} duplicate/pin-notice messages")

    def set_bets(self, every):
        self.day_sels = {}
        for b in every:
            if self.pick(b):
                d = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(MEL).date()
                self.day_sels.setdefault(d, {})[b["sel"]] = float(b.get("stake") or 1)

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
        """Add a settled bet to the day's tally message (one per Melbourne day, edited in place, pinned)."""
        day = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(MEL).date()
        key = f"tally-{day.isoformat()}"
        old = self.msgs.get(key)
        rows, carry, folded = self._tally_rows(old[1]) if old else ({}, [0, 0, 0, 0.0, 0.0], [])
        h = hashlib.sha1(b["sel"].encode()).hexdigest()[:5]
        if h in folded:   # already counted in the "earlier" line
            return
        stake, price = float(b.get("stake") or 1), float(b.get("price") or 1)
        u = 0.0 if res == "void" else ((price - 1) * stake if res == "win" else -stake)
        line = f"{ICON[res]} {'🎯 BOTD · ' if b.get('botd') else ''}**{b['sel']}** · {detail} · {game} · {stake:g}u @ {price:.2f} → `{u:+.2f}u`"
        prev = rows.get(b["sel"])
        if not (prev and prev[0] == res and re.search(r"u @ [\d.]+ → `", prev[1])):   # settled & counted: keep its line
            rows[b["sel"]] = (res, line)
        self._write(day, key, old, rows, carry, folded, f"{res} {b['sel']}")

    def refresh(self, day):
        """Re-render a day's tally (bets-left count) without a new result."""
        key = f"tally-{day.isoformat()}"
        old = self.msgs.get(key)
        if old:
            self._write(day, key, old, *self._tally_rows(old[1]), "refresh")
        elif self.day_sels.get(day):   # bets today but nothing settled yet: post the tally now (all ⏳)
            self._write(day, key, None, {}, [0, 0, 0, 0.0, 0.0], [], "new day")

    def _write(self, day, key, old, rows, carry, folded, why):
        while True:   # a big day: fold the oldest lines into one "earlier" line so the totals stay right
            text = self._tally_text(day, key, rows, carry, folded)
            olds = [k for k, (r, t) in rows.items() if re.search(r"u @ [\d.]+ → `", t)]
            if len(text) <= 1990 or len(rows) <= 1 or not olds:
                break
            folded.append(hashlib.sha1(olds[0].encode()).hexdigest()[:5])
            r0, t0 = rows.pop(olds[0])
            u0, s0 = self._nums(r0, t0)
            carry = [carry[0] + (r0 == "win"), carry[1] + (r0 == "loss"), carry[2] + (r0 == "void"), carry[3] + u0, carry[4] + s0]
        if old and old[1] == text:
            return
        new = key not in self.msgs
        self.upsert(key, text)
        if new:
            try:
                discord_post._call("PUT", f"/channels/{self.cid}/pins/{self.msgs[key][0]}")
            except Exception as e:
                print(f"::notice::couldn't pin tally: {e}")
        print(f"tally: {why} -> {text.split(chr(10))[1]}")

    @staticmethod
    def _nums(res, t):
        """(units, staked) for one tally line; voids stake nothing."""
        m = re.search(r" · ([\d.]+)u @ [\d.]+ → `([+-][\d.]+)u`$", t)
        if not m:   # line from before units were shown
            m2 = re.search(r"`([+-][\d.]+)u`$", t)
            return (float(m2.group(1)) if m2 else 0.0), 0.0
        return float(m.group(2)), (0.0 if res == "void" else float(m.group(1)))

    def _tally_text(self, day, key, rows, carry, folded):
        w, l, v, units, staked = carry
        for r, t in rows.values():
            w, l, v = w + (r == "win"), l + (r == "loss"), v + (r == "void")
            du, ds = self._nums(r, t)
            units, staked = units + du, staked + ds
        rate = f" · {round(100 * w / (w + l))}% hit rate" if w + l else ""
        roi = f" · ROI {100 * units / staked:+.0f}%" if staked else ""
        uicon = "🟢" if units > 0.005 else "🔴" if units < -0.005 else "⚪"
        done = set(rows) | {x for x in self.day_sels.get(day, ()) if hashlib.sha1(x.encode()).hexdigest()[:5] in folded}
        pend = [x for x in self.day_sels.get(day, {}) if x not in done]
        left, total = len(pend), staked + sum(self.day_sels[day][x] for x in pend)
        head = (f"📊 **Today's tally · {day.strftime('%a %-d %b')}**\n"
                f"**✅{w} ❌{l}" + (f" ↩️{v}" if v else "") + f" ⏳{left} {uicon}{units:+.1f}u 💵{staked:g}/{total:g}u**\n"
                f"-# {w}W-{l}L{rate}{roi} · {staked:g}u settled of {total:g}u staked today · {left} left\n")
        early = ""
        if sum(carry[:3]):
            early = f"-# …plus {sum(carry[:3])} earlier: {carry[0]}W-{carry[1]}L-{carry[2]}V · `{carry[3]:+.2f}u` · {carry[4]:g}u staked · f:{''.join(folded)}\n"
        foot = f"\n-# Live tally · updates as bets settle · `live:{key}` · {FOOT[3:]}"
        return head + early + "\n".join(t for _, t in rows.values()) + foot

    @staticmethod
    def _tally_rows(text):
        rows, carry, folded = {}, [0, 0, 0, 0.0, 0.0], []
        for ln in text.split("\n"):
            m = re.match(r"(✅|❌|↩️) (?:🎯 BOTD · )?\*\*(.+?)\*\* · ", ln)
            if m:
                rows[m.group(2)] = ({v: k for k, v in ICON.items()}[m.group(1)], ln)
            c = re.match(r"-# …plus \d+ earlier: (\d+)W-(\d+)L-(\d+)V · `([+-][\d.]+)u` · ([\d.]+)u staked", ln)
            if c:
                carry = [int(c.group(1)), int(c.group(2)), int(c.group(3)), float(c.group(4)), float(c.group(5))]
                f = re.search(r"f:([0-9a-f,]+)", ln)   # folded bet ids: old style `f:a,b`, now f:ab (5 hex each)
                raw = f.group(1).replace(",", "") if f else ""
                folded = [raw[i:i + 5] for i in range(0, len(raw) - 4, 5)]
        return rows, carry, folded


def bp_started(b):
    return datetime.fromisoformat(b["start"].replace("Z", "+00:00")) < datetime.now(timezone.utc) - timedelta(minutes=15)


def one_pass(lives, bets):
    """Update every game once in every tracker channel. Returns True while anything is still to come or in play."""
    bx = boxes()
    where = {}   # box key -> (box, {name: row})
    for k, b in bx.items():
        where[k] = (b, players(b))
    games, pending = {id(l): {} for l in lives}, False
    for b in bets:
        nm = _nm(b["desc"])
        day = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(ET).date().isoformat()
        mine = [(k, bp) for k, bp in where.items() if k.startswith(day + "|") and in_game(b, bp[0])]   # that night's game, his teams only
        hit = next(((k, bp, bp[1][nm]) for k, bp in mine if nm in bp[1]), None) \
            or next(((k, bp, r) for k, bp in mine for r in [find(nm, b["sel"], bp[1])] if r), None)
        if not hit:
            if bp_started(b):
                print(f"::notice::no box-score match for {b['sel']}")
            pending = pending or datetime.fromisoformat(b["start"].replace("Z", "+00:00")) > datetime.now(timezone.utc) - timedelta(hours=4)
            continue
        k, (box, rows), row = hit
        state, clock = game_state(box)
        icon, text, res = progress(b, row, state)
        h, a = box.get("home_team") or {}, box.get("visitor_team") or {}
        g = f"{a.get('abbreviation')} @ {h.get('abbreviation')}"
        for live in lives:
            if not live.pick(b):
                continue
            games[id(live)].setdefault(k, (box, state, clock, []))[3].append(f"{icon} {name(b)} · {text}")
            if res:
                live.result(b, res, text.split(" · ")[0] if res != "void" else "Didn't play", g)
        if state != "final":
            pending = True
    for live in lives:
        gs = games[id(live)]
        print(f"#{live.channel}: {len(bx)} box scores, {sum(len(g[3]) for g in gs.values())}/{sum(map(live.pick, bets))} bets matched, "
              + ", ".join(f"{g[0].get('visitor_team', {}).get('abbreviation')}@{g[0].get('home_team', {}).get('abbreviation')} {g[1]} {g[2]}" for g in gs.values()))
        live.refresh(datetime.now(MEL).date())
        draw_games(live, gs)
    return pending


def draw_games(live, games):
    for k, (box, state, clock, lines) in games.items():
        h, a = box.get("home_team") or {}, box.get("visitor_team") or {}
        score = "" if state == "pre" else f" · {a.get('abbreviation')} {box.get('visitor_team_score', 0)}-{box.get('home_team_score', 0)} {h.get('abbreviation')}"
        tag = f"{str(box.get('date', ''))[:10]}-{a.get('abbreviation')}-{h.get('abbreviation')}"
        note = "final" if state == "final" else "updates every minute"
        text = (f"🏀 **{a.get('full_name', a.get('abbreviation'))} @ {h.get('full_name', h.get('abbreviation'))}** · {clock}{score}\n"
                + "\n".join(lines) + f"\n-# {live.label} · {note} · `live:{tag}`")
        live.upsert(tag, text[:1990])


def main():
    if not KEY:
        sys.exit("NBA_STATS_KEY not set")
    if not discord_post.enabled():
        sys.exit("DISCORD_BOT_TOKEN / DISCORD_GUILD_ID not set")
    bets, every = load_bets(datetime.now(timezone.utc))
    print(f"{len(bets)} NBA prop alerts in the window ({sum(map(is_founder, bets))} founder picks)")
    if not bets and os.environ.get("ONCE") == "1":
        return
    lives = [Live()]
    try:
        lives.append(Live(FOUNDER_CHANNEL, is_founder, "Founder tracker"))
    except LookupError as e:
        print(f"::warning::{e}")
    for l in lives:
        l.set_bets(every)
    end = time.time() + float(os.environ.get("MINUTES", "28")) * 60
    n = 0
    while True:
        if n and n % 5 == 0:   # every 5 min: newly sent alerts and games tipping off later in the run
            refresh_log()
            bets, every = load_bets(datetime.now(timezone.utc))
            for l in lives:
                l.set_bets(every)
        n += 1
        pending = False
        if not bets:
            for l in lives:
                try:
                    l.refresh(datetime.now(MEL).date())
                except Exception as e:
                    print(f"::warning::tally refresh: {e}")
        if bets:
            try:
                pending = one_pass(lives, bets)
            except Exception as e:
                print(f"::warning::live pass failed: {type(e).__name__} {e} {getattr(e, 'url', '')[:60]}")
                pending = True
        # Stay alive through game hours even with nothing live yet: GitHub's cron often skips runs, so a run that
        # quit early left games that tipped off later untracked. Stop once nothing is live and game hours are over.
        if os.environ.get("ONCE") == "1" or time.time() + 60 > end:
            break
        if not pending and datetime.now(MEL).hour >= 19:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
