"""NBA confirmed-lineup + injury alerts, pushed to Jayden's phone with the ntfy app.

Runs on GitHub's servers every ~10 min during the Aussie morning/arvo (see .github/workflows/nba-alerts.yml).
Each run:
  1. pulls today's NBA games (US date), projected lineups and the injury report from BALLDONTLIE (GOAT tier)
  2. compares them with what it saw last run (state.json on the alerts-state branch)
  3. sends ONE push with whatever changed: lineups that just got confirmed, and injury status changes
     for players on teams playing today. Each push has a "Share to WhatsApp" button with the same text,
     ready to forward to the Overlay group.

Env:
  NBA_STATS_KEY   BALLDONTLIE key (secret, never printed)
  NTFY_TOPIC      ntfy topic name (secret: anyone who knows it can read/post to it)
  STATE           path to state.json (read + rewritten)
  TEST=1          send a test push and exit
Usage: python scripts/nba_alerts.py
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

API = "https://api.balldontlie.io/v1"
KEY = os.environ.get("NBA_STATS_KEY", "").strip()
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
STATE = os.environ.get("STATE", "state.json")
ET, SYD = ZoneInfo("America/New_York"), ZoneInfo("Australia/Sydney")
FOOT = "Overlay Tips | 18+ Gamble responsibly"
ORDER = {"Out": 0, "Doubtful": 1, "Questionable": 2, "Day-To-Day": 3, "Probable": 4}


def api(path, params=()):
    url = f"{API}{path}" + ("?" + urllib.parse.urlencode(list(params)) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": KEY, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1)); continue
            raise RuntimeError(f"{path}: HTTP {e.code}") from None


def pages(path, params=(), limit=20):
    cursor, out = None, []
    for _ in range(limit):
        p = list(params) + [("per_page", 100)] + ([("cursor", cursor)] if cursor else [])
        j = api(path, p)
        out += j.get("data") or []
        cursor = (j.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
    return out


def push(title, lines, tags="basketball"):
    body = "\n".join(lines)
    wa = f"{title}\n\n{body}\n\n{FOOT}"
    msg = {"topic": TOPIC, "title": title, "message": body, "tags": tags.split(","),
           "actions": [{"action": "view", "label": "Share to WhatsApp",
                        "url": "https://wa.me/?text=" + urllib.parse.quote(wa[:3500]), "clear": False}]}
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(msg).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def sydtime(iso):
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(SYD)
        return t.strftime("%-I:%M%p").lower()
    except Exception:
        return ""


def pname(p):
    return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()


def short(p):
    return p.get("last_name") or pname(p)


def main():
    if not TOPIC:   # not set up yet: skip quietly so GitHub doesn't email a failure every 10 min
        print("::warning::Add the NTFY_TOPIC secret to start sending alerts"); return
    if os.environ.get("TEST") == "1":
        push("Overlay alerts are working ✅", ["You'll get NBA confirmed lineups + injury news here.",
                                               "Tap 'Share to WhatsApp' on any alert to forward it."])
        print("test push sent"); return
    if not KEY:
        sys.exit("NBA_STATS_KEY not set")

    try:
        state = json.load(open(STATE))
    except Exception:
        state = {}
    first_run = "injuries" not in state
    day = datetime.now(ET).strftime("%Y-%m-%d")      # US game date (Aussie morning = US evening)

    games = api("/games", [("dates[]", day), ("per_page", 100)]).get("data") or []
    teams, opp, tip = {}, {}, {}
    for g in games:
        h, v = g.get("home_team") or {}, g.get("visitor_team") or {}
        for a, b, at in ((h, v, "vs"), (v, h, "@")):
            if a.get("id"):
                teams[a["id"]] = a.get("abbreviation") or a.get("name") or "?"
                opp[a["id"]] = f"{at} {b.get('abbreviation', '?')}"
                tip[a["id"]] = sydtime(g.get("datetime") or "")
    playing = set(teams)

    # ---- confirmed lineups ----
    sent = state.get("lineups", {}) if state.get("day") == day else {}
    lineup_lines = []
    if games:
        for lu in pages("/projected_lineups", [("dates[]", day)]):
            if lu.get("status") != "confirmed":
                continue
            k = f"{lu.get('game_id')}-{lu.get('team_id')}"
            names = [short(x.get("player") or {}) for x in lu.get("lineup") or []]
            sig = ",".join(names)
            if sent.get(k) == sig:
                continue
            tid = lu.get("team_id")
            head = f"{teams.get(tid, '?')} {opp.get(tid, '')}".strip() + (f" ({tip[tid]})" if tip.get(tid) else "")
            change = " (CHANGED)" if k in sent else ""
            lineup_lines.append(f"✅ {head}{change}: {', '.join(names)}")
            sent[k] = sig

    # ---- injuries (whole league, so tomorrow's teams are already known; only alert for today's) ----
    old = state.get("injuries", {})
    new, inj_lines = {}, []
    for r in pages("/player_injuries"):
        p = r.get("player") or {}
        if not p.get("id"):
            continue
        new[str(p["id"])] = {"n": pname(p), "t": p.get("team_id"), "s": r.get("status") or "",
                             "r": r.get("return_date") or "", "d": (r.get("description") or "")[:140]}
    if not first_run:
        changes = []
        for pid, x in new.items():
            o = old.get(pid)
            if x["t"] not in playing or (o and o["s"] == x["s"]):
                continue
            was = f" (was {o['s']})" if o else ""
            ret = f", return {x['r']}" if x["r"] and x["s"] == "Out" else ""
            changes.append((ORDER.get(x["s"], 9), f"🚑 {x['n']} ({teams.get(x['t'], '?')}): {x['s']}{was}{ret}"))
        for pid, o in old.items():
            if pid not in new and o.get("t") in playing:
                changes.append((8, f"🟢 {o['n']} ({teams.get(o['t'], '?')}): off the injury report (was {o['s']})"))
        inj_lines = [l for _, l in sorted(changes)]

    # ---- send ----
    if lineup_lines or inj_lines:
        if lineup_lines and inj_lines:
            title = "NBA team news: lineups + injuries"
        elif lineup_lines:
            title = "NBA confirmed starters" if len(lineup_lines) > 1 else "NBA confirmed starters: " + lineup_lines[0][2:].split(": ")[0]
        else:
            title = "NBA injury update" if len(inj_lines) > 1 else "NBA injury: " + inj_lines[0][2:]
        body = lineup_lines + ([""] if lineup_lines and inj_lines else []) + inj_lines
        push(title, body[:40])

    state = {"day": day, "lineups": sent, "injuries": new,
             "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    json.dump(state, open(STATE, "w"), separators=(",", ":"))
    print(f"{day}: {len(games)} games, {len(lineup_lines)} lineup alerts, {len(inj_lines)} injury alerts"
          + (" (first run: injuries seeded, nothing sent)" if first_run else ""))


if __name__ == "__main__":
    main()
