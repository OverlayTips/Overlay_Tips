"""Main NBA player prop lines for the free Live page (live.html).

Published UNENCRYPTED as odds-data/lines.json, so it carries only the line itself (e.g. Tatum
Points 26.5): no prices, fair odds or edges, which stay on the members' odds board.
  {"updated": TS, "games": [{"home", "away", "start", "players": {name: {stat label: line}}}]}
"""
from datetime import datetime

import hot, picks


def build(prop_events, now, ts):
    games = []
    for ev in prop_events:
        if ev.get("sport_key") != "basketball_nba":
            continue
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if (now - start).total_seconds() > 6 * 3600:   # keep live games, drop old ones
            continue
        players = {}
        for mk, desc, sides, main in picks._groups(ev):
            if mk not in hot.STAT or not desc:          # main markets only (no _alternate ladders)
                continue
            pt = main.get("Over", main.get("Under"))
            if pt is None:
                continue
            players.setdefault(desc, {})[hot.STAT[mk][0]] = float(pt)
        if players:
            games.append({"home": ev.get("home_team"), "away": ev.get("away_team"),
                          "start": ev["commence_time"], "players": players})
    games.sort(key=lambda g: g["start"])
    return {"updated": ts, "games": games}
