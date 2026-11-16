"""NFL injury + inactive alerts, pushed to Jayden's phone with the ntfy app (same topic as the NBA alerts).

Runs on GitHub's servers during the Aussie morning/arvo (see .github/workflows/nfl-alerts.yml). Each run:
  1. pulls this week's NFL player designations from BALLDONTLIE (NFL GOAT tier): game status
     (questionable / doubtful / out) and game-day actives / inactives (out ~90 min before kick-off)
  2. compares them with last run (nfl_state.json on the nfl-alerts-state branch)
  3. sends ONE push with what changed for QBs, RBs, WRs and TEs in games that haven't kicked off:
     new or changed injury designations, players ruled inactive, and questionable/doubtful players
     confirmed active. Each push has an "Open Discord" button.
  4. for anyone out, doubtful or inactive, adds a "who benefits" line from the team's season usage
     (BALLDONTLIE season stats + depth chart): the backup QB, the next running back's carries, or the
     receivers in line for the targets he leaves behind.
The first run only records what's there (no flood of old news).

Env: NBA_STATS_KEY (BALLDONTLIE key), NTFY_TOPIC, DISCORD_BOT_TOKEN + DISCORD_GUILD_ID (#nfl-lineups), STATE (path to nfl_state.json), TEST=1 (test push)
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


def _usage(season, team_id, cache):
    """{player id: per-game usage} for a team's season, plus its depth chart. Best effort."""
    if team_id in cache:
        return cache[team_id]
    out = {"p": {}, "depth": {}}
    try:
        for r in base.pages("/season_stats", [("season", season), ("team_id", team_id)], limit=3):
            pl, gp = r.get("player") or {}, r.get("games_played") or 0
            if not pl.get("id") or gp <= 0:
                continue
            out["p"][pl["id"]] = {"n": f"{pl.get('first_name', '')} {pl.get('last_name', '')}".strip(),
                                  "pos": pl.get("position_abbreviation") or "", "gp": gp,
                                  "car": (r.get("rushing_attempts") or 0) / gp, "tgt": (r.get("receiving_targets") or 0) / gp,
                                  "ryd": (r.get("rushing_yards") or 0) / gp, "recyd": (r.get("receiving_yards") or 0) / gp,
                                  "patt": (r.get("passing_attempts") or 0) / gp}
    except Exception as e:
        print("season stats failed:", type(e).__name__, e)
    try:
        for e in base.api(f"/teams/{team_id}/roster").get("data") or []:
            pl = e.get("player") or {}
            if pl.get("id") and e.get("position"):
                out["depth"].setdefault(e["position"], []).append((e.get("depth") or 9, pl["id"], e.get("player_name") or ""))
    except Exception as e:
        print("roster failed:", type(e).__name__, e)
    cache[team_id] = out
    return out


def _f(x):
    return f"{x:.1f}".rstrip("0").rstrip(".")


def benefits(d, season, cache, gone):
    """One short 'who benefits' line for a player who's out / doubtful / inactive, or None."""
    p, t = d.get("player") or {}, d.get("team") or {}
    pos, pid = p.get("position_abbreviation") or "", p.get("id")
    if not t.get("id") or not pid:
        return None
    u = _usage(season, t["id"], cache)
    me, ps = u["p"].get(pid), u["p"]
    avail = {i: x for i, x in ps.items() if i != pid and i not in gone}
    if pos == "QB":
        nxt = sorted((x for x in u["depth"].get("QB", []) if x[1] != pid and x[1] not in gone), key=lambda x: x[0])
        if nxt:
            b = ps.get(nxt[0][1])
            extra = f" ({_f(b['patt'])} att/g in {b['gp']} games)" if b and b["patt"] >= 1 else " (little game time this season)"
            return f"↳ {nxt[0][2]} in line to start{extra}. Passing props for the {t.get('abbreviation', '')} receivers take a hit."
        return f"↳ Backup QB starts: a downgrade for the {t.get('abbreviation', '')} passing game."
    if pos in ("RB", "FB"):
        rbs = sorted((x for x in avail.values() if x["pos"] in ("RB", "FB") and x["car"] >= 1), key=lambda x: -x["car"])[:2]
        if not rbs:
            return None
        lost = f"{_f(me['car'])} carries/g" if me and me["car"] else "his carries"
        return f"↳ {lost} up for grabs: " + ", ".join(f"{x['n']} ({_f(x['car'])} car, {_f(x['ryd'])} rush yds/g)" for x in rbs)
    if pos in ("WR", "TE"):
        rec = sorted((x for x in avail.values() if x["pos"] in ("WR", "TE", "RB") and x["tgt"] >= 2), key=lambda x: -x["tgt"])[:3]
        if not rec:
            return None
        lost = f"{_f(me['tgt'])} targets/g" if me and me["tgt"] else "his targets"
        return f"↳ {lost} to spread around: " + ", ".join(f"{x['n']} ({_f(x['tgt'])} tgt, {_f(x['recyd'])} yds/g)" for x in rec)
    return None


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
    gone = {(d.get("player") or {}).get("id") for d in des if d.get("game_status") == "out" or d.get("active") is False}
    ucache = {}
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
            if act is False or (gs in ("out", "doubtful") and act is not True):
                try:
                    why = benefits(d, season, ucache, gone)
                    if why:
                        line += "\n   " + why
                except Exception as e:
                    print("benefits failed:", type(e).__name__, e)
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
        try:   # members' app alerts (web push): Daily card + VIP
            import web_push
            web_push.send("news", f"🏈 NFL injury news ({n})", [l for l in out[:-1] if l][:8], "https://overlay.tips/live.html", "core")
        except Exception as e:
            print(f"::warning::app push failed: {e}")
        try:   # Discord: same news into #nfl-lineups
            import discord_post as dp
            dp.send("nfl-lineups", f"🚑 NFL injury news ({n})", out[:-1], "https://overlay.tips/lineups.html",
                    "-# Overlay Wire · 18+ · Gamble responsibly · 1800 858 858")
        except Exception as e:
            print(f"::warning::discord post failed: {e}")
        print(f"pushed {n} updates")
    else:
        print("no changes" if state is not None else f"first run: recorded {len(cur)} designations")
    json.dump({"wk": [season, week, stype], "d": {**seen, **cur}, "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
              open(STATE, "w"), separators=(",", ":"))


if __name__ == "__main__":
    main()
