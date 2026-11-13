"""NBA availability for the value models: injury status + confirmed starters, from the NBA alerts feed.

Sources (both written by scripts/nba_alerts.py, so no extra API calls):
  alerts-state/state.json   "injuries": league-wide injury report {player_id: {n, t, s, r, d}}
  lineups-data/lineups.json today's games with confirmed/projected starters

check(name, team=None) -> {"s": status or "", "start": True/False/None, "drop": bool, "flag": short text}
  drop  = Out / Doubtful: don't show the bet, don't alert it
  flag  = "Q" (Questionable), "GTD" (Day-To-Day), "Bench" (team lineup confirmed, not starting), "" otherwise
"""
import json, os, re, unicodedata

STATE = os.environ.get("INJ_STATE", "alerts-state/state.json")
LINEUPS = os.environ.get("LINEUPS", "lineups-data/lineups.json")
DROP = {"Out", "Doubtful"}
FLAG = {"Questionable": "Q", "Day-To-Day": "GTD", "Probable": ""}
_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}


def key(n):
    n = unicodedata.normalize("NFD", str(n or "")).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[.'`’]", "", n).replace("-", " ")
    return "".join(t for t in n.split() if t not in _SUFFIX)


def _load(p):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {}


class Availability:
    def __init__(self, state_path=STATE, lineups_path=LINEUPS):
        st, lu = _load(state_path), _load(lineups_path)
        self.updated = lu.get("updated") or st.get("updated") or ""
        self.inj = {key(x.get("n")): x.get("s") or "" for x in (st.get("injuries") or {}).values()}
        self.starters, self.confirmed = set(), set()   # player keys / team abbreviations with a confirmed lineup
        for g in lu.get("games") or []:
            if (g.get("status") or "") == "Final":
                continue
            for side in ("away", "home"):
                t = g.get(side) or {}
                for x in t.get("injuries") or []:   # same feed, but newer than state.json if lineups ran later
                    self.inj[key(x.get("n"))] = x.get("s") or ""
                if t.get("status") == "confirmed":
                    self.confirmed.add(t.get("abbr"))
                    self.starters |= {key(s.get("n")) for s in t.get("starters") or []}
        self.ok = bool(self.inj or self.confirmed)

    def check(self, name, team=None):
        k = key(name)
        s = self.inj.get(k, "")
        start = True if k in self.starters else (False if team and team in self.confirmed else None)
        flag = FLAG.get(s, "")
        if not flag and start is False:
            flag = "Bench"
        return {"s": s, "start": start, "drop": s in DROP, "flag": flag}

    def map(self):
        """Compact {player key: status} for the odds page (Out/Doubtful/Questionable/Day-To-Day only)."""
        return {k: v for k, v in self.inj.items() if v in DROP or v in FLAG and FLAG[v]}
