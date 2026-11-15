"""Main NBA and NFL player prop lines for the members' Live page (live.html).

fetch_odds.py publishes it as odds-data/lines.json, encrypted with the odds page password. It carries
only the line itself (e.g. Tatum Points 26.5): no prices, fair odds or edges.
  {"updated": TS, "games": [{"lg": "NBA"|"NFL", "home", "away", "start", "players": {name: {stat label: line}}}]}
"""
from datetime import datetime

import hot, picks

# NFL prop markets (The Odds API keys) -> labels the Live page and bet tracker know
NFL_STAT = {
    "player_pass_yds": "Passing Yards", "player_pass_tds": "Passing TDs", "player_pass_completions": "Completions",
    "player_pass_attempts": "Pass Attempts", "player_pass_interceptions": "Interceptions",
    "player_rush_yds": "Rushing Yards", "player_rush_attempts": "Rush Attempts",
    "player_receptions": "Receptions", "player_reception_yds": "Receiving Yards",
}
LEAGUES = {"basketball_nba": ("NBA", lambda mk: hot.STAT[mk][0] if mk in hot.STAT else None),
           "americanfootball_nfl": ("NFL", NFL_STAT.get)}


def build(prop_events, now, ts):
    games = []
    for ev in prop_events:
        lg = LEAGUES.get(ev.get("sport_key"))
        if not lg:
            continue
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if (now - start).total_seconds() > 6 * 3600:   # keep live games, drop old ones
            continue
        players = {}
        for mk, desc, sides, main in picks._groups(ev):
            label = lg[1](mk)
            if not label or not desc:                   # main markets only (no _alternate ladders)
                continue
            pt = main.get("Over", main.get("Under"))
            if pt is None:
                continue
            players.setdefault(desc, {})[label] = float(pt)
        if players:
            games.append({"lg": lg[0], "home": ev.get("home_team"), "away": ev.get("away_team"),
                          "start": ev["commence_time"], "players": players})
    games.sort(key=lambda g: g["start"])
    return {"updated": ts, "games": games}
