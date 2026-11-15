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

# Each team's depth chart lists its own base formation (one row per position, depth 1 = starter), e.g.
# offence QB RB WR WR-2 WR-3 TE LT LG C RG RT, defence LDE LDT RDT RDE WLB MLB SLB LCB RCB FS SS (4-3) or
# LDE NT RDE LILB RILB WLB SLB ... (3-4). We fill every one of those rows and place it on the field.
OFF_KEYS = ["WR", "WR-3", "LT", "LG", "C", "RG", "RT", "TE", "WR-2", "QB", "FB", "RB"]
DL = ["LDE", "LDT", "DT", "NT", "RDT", "RDE", "DE"]
LBS = ["WLB", "LOLB", "LILB", "MLB", "ILB", "RILB", "ROLB", "SLB", "OLB", "LB"]
DBS = ["LCB", "NB", "FS", "SS", "S", "RCB", "CB"]
ST = ["PK", "P"]
OFF_XY = {"WR": (8, 25), "WR-2": (92, 25), "WR-3": (19, 41), "LT": (32, 25), "LG": (41, 25), "C": (50, 25),
          "RG": (59, 25), "RT": (68, 25), "TE": (78, 27), "QB": (50, 55), "FB": (50, 67), "RB": (50, 79)}
DB_XY = {"LCB": (8, 70), "RCB": (92, 70), "CB": (92, 70), "NB": (19, 60), "FS": (33, 20), "SS": (67, 20), "S": (67, 20)}
LABEL = {"LDE": "DE", "RDE": "DE", "LDT": "DT", "RDT": "DT", "LILB": "LB", "RILB": "LB", "MLB": "LB", "ILB": "LB",
         "WLB": "LB", "SLB": "LB", "OLB": "LB", "LOLB": "LB", "ROLB": "LB", "LCB": "CB", "RCB": "CB", "FS": "S",
         "SS": "S", "WR-2": "WR", "WR-3": "WR", "PK": "K"}
TAG = {"questionable": "Q", "doubtful": "D"}


def _spread(keys, y, lo=24, hi=76):
    n = len(keys)
    return {k: (round(lo + (hi - lo) * i / (n - 1)) if n > 1 else 50, y) for i, k in enumerate(keys)}


def pname(p):
    return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()


def build(roster, des_by_pid, gone):
    """Starters for every row of one team's depth chart, with field spots. roster: [(pos, depth, order, pid, name)]."""
    starter_flags = any(d.get("starter") for d in des_by_pid.values())
    rows = {}
    for r in roster:
        rows.setdefault(r[0], []).append(r)
    used = set()

    def pick(key, fb=()):
        cands = [r for r in rows.get(key, []) if r[3] not in used and r[3] not in gone]
        if not cands:   # nobody left at this spot: next man up from the same unit
            for k in fb:
                p = pick(k)
                if p:
                    p["pos"] = key
                    p["slot"] = LABEL.get(key, key)
                    return p
            return None
        # BALLDONTLIE's game-day starter flag first (when it has one), then depth chart order
        cands.sort(key=lambda r: (not (des_by_pid.get(r[3]) or {}).get("starter") if starter_flags else 0, r[1], r[2]))
        if not cands:
            return None
        r = cands[0]
        used.add(r[3])
        d = des_by_pid.get(r[3]) or {}
        p = {"slot": LABEL.get(key, key), "n": r[4], "pos": key}
        if TAG.get(d.get("game_status")) and d.get("active") is not True:
            p["inj"] = TAG[d["game_status"]]
        return p

    def place(keys, xy, fb=(), first=()):
        out = []
        for k in keys:
            p = pick(k, list(first) + [x for x in keys if x != k] + list(fb))
            if p:
                p["x"], p["y"] = xy[k]
                out.append(p)
        return out

    ok = [k for k in OFF_KEYS if k in rows and not (k == "FB" and "WR-3" in rows)]   # 3-WR sets: no fullback
    off = place(ok, OFF_XY)
    dl, lb = [k for k in DL if k in rows], [k for k in LBS if k in rows]
    db = [k for k in DBS if k in rows]
    if len(dl) + len(lb) + len(db) > 11 and "NB" in db:
        db.remove("NB")   # base defence when the chart lists a nickel back as well as 3 linebackers
    dxy = {**_spread(dl, 76), **_spread(lb, 50, 20, 80), **DB_XY}
    de = place(dl, dxy, lb) + place(lb, dxy, first=["NB"]) + place(db, dxy)
    return {"off": off, "def": de, "st": [p for k in ST for p in [pick(k)] if p]}


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
