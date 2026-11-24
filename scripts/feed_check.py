"""Feed check: what the odds feed actually has for one bookie, for the bonus bet converter.
Prints counts and selection names only (no prices: Actions logs on this repo are public).

Env: ODDS_PAGE_PASSWORD, BOOK (default dabble_au), SPORT (default soccer_uefa_champs_league), OUT (default odds-data).
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_odds

BOOK = os.environ.get("BOOK", "dabble_au")
SPORT = os.environ.get("SPORT", "soccer_uefa_champs_league")
OUT = os.environ.get("OUT", "odds-data")

d = fetch_odds.decrypt(json.load(open(os.path.join(OUT, "data.json"))))
O = d["odds"]
evs = O["events"]
print(f"feed updated {O['updated']} · {len(evs)} games · errors: {O.get('errors') or 'none'}")
print(f"{SPORT} checked this run: {SPORT in (O.get('sports_checked') or [])}")

per = Counter(e["sport_key"] for e in evs if any(b["key"] == BOOK for b in e["bookmakers"]))
print(f"\n{BOOK} is on {sum(per.values())} games: " + ", ".join(f"{k} {n}" for k, n in per.most_common()))
lay = Counter(e["sport_key"] for e in evs for b in e["bookmakers"]
              if b["key"] == "betfair_ex_au" and any(m["key"].endswith("_lay") for m in b["markets"]))
print(f"Betfair lays on {sum(lay.values())} games: " + ", ".join(f"{k} {n}" for k, n in lay.most_common()))

print(f"\n---- {SPORT} ----")
for e in (x for x in evs if x["sport_key"] == SPORT):
    bk = {b["key"]: b for b in e["bookmakers"]}
    print(f"\n{e['home_team']} v {e['away_team']} ({e['commence_time']}) · books: {', '.join(sorted(bk))}")
    for key in (BOOK, "betfair_ex_au"):
        b = bk.get(key)
        if not b:
            print(f"  {key}: NOT IN FEED")
            continue
        for m in b["markets"]:
            print(f"  {key} {m['key']}: " + " | ".join(o["name"] + (f" {o['point']}" if o.get("point") is not None else "")
                                                   for o in m["outcomes"]))
