"""NFL injury + inactive alerts, pushed to Jayden's phone with the ntfy app (same topic as the NBA alerts).

Runs on GitHub's servers during the Aussie morning/arvo (see .github/workflows/nfl-alerts.yml). Each run:
  1. pulls this week's NFL player designations from BALLDONTLIE (NFL GOAT tier): game status
     (questionable / doubtful / out) and game-day actives / inactives (out ~90 min before kick-off)
  2. compares them with last run (nfl_state.json on the nfl-alerts-state branch)
  3. sends ONE push with what changed for QBs, RBs, WRs and TEs in games that haven't kicked off:
     new or changed injury designations, players ruled inactive, and questionable/doubtful players
     confirmed active. Each push has a "Share to WhatsApp" button with the same text.
The first run only records what's there (no flood of old news).

Env: NBA_STATS_KEY (BALLDONTLIE key), NTFY_TOPIC, STATE (path to nfl_state.json), TEST=1 (test push)
"""
import json, os, re, sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nba_alerts as base   # same ntfy push + BALLDONTLIE helpers

base.API = "https://api.balldontlie.io/nfl/v1"
STATE = os.environ.get("STATE", "nfl_state.json")
SYD = ZoneInfo("Australia/Sydney")
POS = {"QB", "RB", "WR", "TE", "FB"}
LABEL = {"out": "OUT", "doubtful": "Doubtful", "questionable": "Questionable"}
RANK = {"out": 0, "doubtful": 1, "questionable": 2}


def kick(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(SYD).strftime("%a %-I:%M%p").replace("AM", "am").replace("PM", "pm")
    except Exception:
        return ""


def main():
    if os.environ.get("TEST") == "1":
        base.push("🏈 NFL alerts test", ["NFL injury + inactive alerts are set up. You'll get news here on game weeks."], tags="football")
        print("test push sent"); return
    if not base.KEY:
        print("NBA_STATS_KEY not set"); return
    try:
        state = json.load(open(STATE))
    except Exception:
        state = None
    des = base.pages("/player_designations", limit=30)   # current week (next unfinished game decides it)
    if not des:
        print("no designations this week"); return
    season, week, stype = des[0].get("season"), des[0].get("week"), des[0].get("season_type")
    st_code = {"preseason": 1, "regular": 2, "postseason": 3}.get(stype, 2)
    games = {g["id"]: g for g in base.pages("/games", [("seasons[]", season), ("weeks[]", week), ("season_types[]", st_code)])}
    now = datetime.now(timezone.utc)
    seen = (state or {}).get("d") if (state or {}).get("wk") == [season, week, stype] else {}
    seen = seen or {}
    news = {}   # game id -> lines
    cur = {}
    for d in des:
        p, t, g = d.get("player") or {}, d.get("team") or {}, games.get(d.get("game_id"))
        if not g or (p.get("position_abbreviation") or "") not in POS:
            continue
        k = str(d["id"])
        gs, act = d.get("game_status"), d.get("active")
        cur[k] = [gs, act]
        if state is None or g.get("status_state") not in ("scheduled", "delayed", None):
            continue   # first run, or the game has started
        was = seen.get(k) or [None, None]
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        who = f"{name} ({t.get('abbreviation', '')} {p.get('position_abbreviation', '')})"
        why = f" – {d['injury']}" if d.get("injury") else ""
        line = None
        if act is False and was[1] is not False:
            line = f"❌ {who} INACTIVE{why}"
        elif act is True and was[1] is not True and (gs or was[0]) in ("questionable", "doubtful"):
            line = f"✅ {who} ACTIVE (was {LABEL[gs or was[0]].lower()})"
        elif gs in LABEL and gs != was[0]:
            if was[0] in LABEL:
                line = f"{'🔻' if RANK[gs] < RANK[was[0]] else '🔺'} {who}: {LABEL[was[0]]} → {LABEL[gs]}{why}"
            else:
                line = f"{'🚨' if gs == 'out' else '⚠️'} {who} {LABEL[gs]}{why}"
        elif was[0] in LABEL and gs is None and act is None:
            line = f"🟢 {who} off the injury report (was {LABEL[was[0]].lower()})"
        if line:
            news.setdefault(g["id"], []).append((RANK.get(gs, 3) if act is not True else 4, line))
    if news:
        out = []
        for gid in sorted(news, key=lambda i: games[i].get("date") or ""):
            g = games[gid]
            out.append(f"{(g.get('visitor_team') or {}).get('abbreviation')} @ {(g.get('home_team') or {}).get('abbreviation')} · {kick(g.get('date') or '')}")
            out += [l for _, l in sorted(news[gid])]
            out.append("")
        n = sum(len(v) for v in news.values())
        base.push(f"🏈 NFL injury news ({n})", out[:-1], tags="football")
        print(f"pushed {n} updates")
    else:
        print("no changes" if state is not None else f"first run: recorded {len(cur)} designations")
    json.dump({"wk": [season, week, stype], "d": {**seen, **cur}, "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
              open(STATE, "w"), separators=(",", ":"))


if __name__ == "__main__":
    main()
