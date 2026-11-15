"""NFL starting lineups for the Overlay Wire page (lineups.html, NFL tab), with a position map.

For every game this week that hasn't finished, builds both teams' starting 11 on offence and defence (plus K/P)
from BALLDONTLIE's depth charts (/teams/<id>/roster), then fixes it up with the week's player designations:
  - players ruled out or game-day inactive are dropped and the next man on the depth chart moves up
  - once the team's game-day actives/inactives are out (~90 min before kick-off) or BALLDONTLIE flags
    starters, the team flips from "projected" to "confirmed"
Writes st/nfl_lineups.json (published to the nfl-lineups-data branch by .github/workflows/nfl-alerts.yml).

Env: NBA_STATS_KEY (BALLDONTLIE NFL GOAT key), OUT (default st/nfl_lineups.json)
"""
import json, os, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nba_alerts as base

base.API = "https://api.balldontlie.io/nfl/v1"
OUT = os.environ.get("OUT", "st/nfl_lineups.json")

QB, RB, FB, TE = {"QB"}, {"RB", "HB"}, {"FB"}, {"TE"}
WR = {"WR", "WR1", "WR2", "WR3", "LWR", "RWR", "SWR", "SLOT", "X", "Y", "Z"}
LT, LG, C, RG, RT = {"LT", "OT", "T"}, {"LG", "OG", "G"}, {"C"}, {"RG", "OG", "G"}, {"RT", "OT", "T"}
DE = {"DE", "LDE", "RDE", "EDGE", "LEDG", "REDG"}
DT = {"DT", "LDT", "RDT", "NT", "DL"}
LB = {"LB", "MLB", "ILB", "WLB", "SLB", "LILB", "RILB", "OLB", "LOLB", "ROLB", "WILL", "MIKE", "SAM"}
CB = {"CB", "LCB", "RCB"}
S = {"S", "SS", "FS"}
# (slot, positions to fill it from, fallback positions when none of those are left)
OFF = [("WR", WR, ()), ("LT", LT, ()), ("LG", LG, ()), ("C", C, ()), ("RG", RG, ()), ("RT", RT, ()), ("TE", TE, FB),
       ("WR", WR, ()), ("WR", WR, TE), ("QB", QB, ()), ("RB", RB, FB)]
DEF = [("DE", DE, ()), ("DT", DT, ()), ("DT", DT, DE), ("DE", DE, LB), ("LB", LB, ()), ("LB", LB, ()),
       ("CB", CB, ()), ("CB", CB, ()), ("S", S, ()), ("S", S, CB), ("LB", LB, CB | S)]   # last: 3rd LB or the nickel back
ST = [("K", {"PK", "K"}, ()), ("P", {"P"}, ())]
TAG = {"questionable": "Q", "doubtful": "D"}


def pname(p):
    return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()


def build(roster, des_by_pid, gone):
    """Starters by slot from one team's depth chart. roster: [(pos, depth, order, pid, name)]."""
    used, starter_flags = set(), any(d.get("starter") for d in des_by_pid.values())

    def pick(keys, fb=()):
        cands = [r for r in roster if r[0] in keys and r[3] not in used and r[3] not in gone]
        if not cands and fb:
            return pick(fb)
        # game-day starter flag first (when BALLDONTLIE has it), then depth, then depth-chart order
        cands.sort(key=lambda r: (not (des_by_pid.get(r[3]) or {}).get("starter") if starter_flags else 0, r[1], r[2]))
        if not cands:
            return None
        r = cands[0]
        used.add(r[3])
        d = des_by_pid.get(r[3]) or {}
        out = {"n": r[4], "pos": r[0]}
        if TAG.get(d.get("game_status")) and d.get("active") is not True:
            out["inj"] = TAG[d["game_status"]]
        return out

    def side(slots):
        out = [dict(slot=s, **p) for s, ks, fb in slots for p in [pick(ks, fb)] if p]
        for p in out:
            if p["slot"] == "LB" and p["pos"] in CB | S:
                p["slot"] = "NB"   # nickel defence: a fifth defensive back instead of a third linebacker
        return out

    return {"off": side(OFF), "def": side(DEF), "st": side(ST)}


def main():
    if not base.KEY:
        print("NBA_STATS_KEY not set"); return
    now = datetime.now(timezone.utc)
    des = base.pages("/player_designations", limit=30)
    if des:
        season, week, stype = des[0].get("season"), des[0].get("week"), des[0].get("season_type")
    else:
        season, week, stype = None, None, None
    st_code = {"preseason": 1, "regular": 2, "postseason": 3}.get(stype, 2)
    if season is None:
        season = now.year if now.month >= 8 else now.year - 1
        up = [g for g in base.pages("/games", [("seasons[]", season)], limit=5)
              if g.get("status_state") != "final" and g.get("week")]
        if not up:
            print("no upcoming games"); return
        week = min(g["week"] for g in up)
    games = [g for g in base.pages("/games", [("seasons[]", season), ("weeks[]", week), ("season_types[]", st_code)])
             if g.get("status_state") != "final"]
    games.sort(key=lambda g: g.get("date") or "")
    by_team = {}
    for d in des:
        tid = (d.get("team") or {}).get("id")
        pid = (d.get("player") or {}).get("id")
        if tid and pid:
            by_team.setdefault(tid, {})[pid] = d
    posvals, out = set(), []
    for g in games:
        row = {"id": g["id"], "tip": g.get("date"), "status": g.get("status_state") or "scheduled", "week": week}
        for side, key in (("away", "visitor_team"), ("home", "home_team")):
            t = g.get(key) or {}
            tdes = by_team.get(t.get("id"), {})
            gone = {pid for pid, d in tdes.items() if d.get("game_status") == "out" or d.get("active") is False}
            roster = []
            try:
                for i, e in enumerate(base.api(f"/teams/{t['id']}/roster").get("data") or []):
                    pl = e.get("player") or {}
                    pos = str(e.get("position") or "").upper()
                    if pl.get("id") and pos:
                        posvals.add(pos)
                        roster.append((pos, e.get("depth") or 9, i, pl["id"], e.get("player_name") or pname(pl)))
            except Exception as ex:
                print(f"roster {t.get('abbreviation')} failed:", ex)
            lu = build(roster, tdes, gone) if roster else {"off": [], "def": [], "st": []}
            posd = {pid: (d.get("player") or {}).get("position_abbreviation") or "" for pid, d in tdes.items()}
            confirmed = any(d.get("active") is not None or d.get("starter") for d in tdes.values())
            inactive = sorted(pname(d.get("player") or {}) for d in tdes.values() if d.get("active") is False)
            inj = sorted(({"n": pname(d.get("player") or {}), "pos": posd[pid], "s": d["game_status"].title(),
                           **({"r": d["injury"]} if d.get("injury") else {})}
                          for pid, d in tdes.items() if d.get("game_status") and d.get("active") is not False),
                         key=lambda x: ({"Out": 0, "Doubtful": 1}.get(x["s"], 2), x["n"]))
            row[side] = {"abbr": t.get("abbreviation") or "", "name": t.get("full_name") or "",
                         "status": "confirmed" if confirmed else "projected", **lu,
                         "inactive": inactive, "injuries": inj}
        out.append(row)
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    json.dump({"updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "season": season, "week": week,
               "games": out, "posvals": sorted(posvals)}, open(OUT, "w"), separators=(",", ":"))
    print(f"week {week}: {len(out)} games, positions seen: {' '.join(sorted(posvals))}")


if __name__ == "__main__":
    main()
