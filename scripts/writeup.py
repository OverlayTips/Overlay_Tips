"""Tipster-style write-up for a Hot stats prop (NBA + NBL), built only from numbers we already have.

writeup(item) -> str   a short paragraph: form, last game + workload, matchup, head-to-head, role/minutes,
                       pace, price, injury flag, then a one-line lean. Every number comes from the item itself,
                       so it can't make anything up. Used by hot.py (stored as item["why"]), the push alerts,
                       the Hot stats + Odds pages and the daily preview.
"""
import re
from datetime import datetime, timedelta

NBA = {"ATL": "Hawks", "BOS": "Celtics", "BKN": "Nets", "CHA": "Hornets", "CHI": "Bulls", "CLE": "Cavaliers",
       "DAL": "Mavericks", "DEN": "Nuggets", "DET": "Pistons", "GSW": "Warriors", "HOU": "Rockets", "IND": "Pacers",
       "LAC": "Clippers", "LAL": "Lakers", "MEM": "Grizzlies", "MIA": "Heat", "MIL": "Bucks", "MIN": "Timberwolves",
       "NOP": "Pelicans", "NYK": "Knicks", "OKC": "Thunder", "ORL": "Magic", "PHI": "76ers", "PHX": "Suns",
       "POR": "Trail Blazers", "SAC": "Kings", "SAS": "Spurs", "TOR": "Raptors", "UTA": "Jazz", "WAS": "Wizards"}
POS = {"G": "guards", "F": "forwards", "C": "centres"}
SUFFIX = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def _team(x):
    return NBA.get(x, x) if x else "them"


def _the(x):
    """'the Cavaliers' for NBA nicknames, plain name otherwise (NBL names come in full)."""
    return f"the {NBA[x]}" if x in NBA else (x or "them")


def _last(name):
    w = str(name).split()
    while len(w) > 1 and w[-1].lower() in SUFFIX:
        w.pop()
    return w[-1] if w else str(name)


def _stat(label):
    s = str(label)
    for a, b in (("Pts", "points"), ("Reb", "rebounds"), ("Ast", "assists"), ("Blk", "blocks"), ("Stl", "steals")):
        s = re.sub(rf"\b{a}\b", b, s)
    return s.lower()


def _ord(n):
    return f"{n}{'tsnrhtdd'[(n // 10 % 10 != 1) * (n % 10 < 4) * n % 10::4]}"


def _num(x):
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def _list(xs):
    xs = [str(x) for x in xs]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _cap(s):
    return s[:1].upper() + s[1:]


def _local_day(iso):
    """US game day of a UTC tip-off (NBA box score dates are US dates); NBL keeps the Australian date."""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return t, (t - timedelta(hours=9)).date()


def writeup(it):
    name, last = it.get("player", "He"), _last(it.get("player", "He"))
    pt, stat = it.get("point"), _stat(it.get("stat", ""))
    n = int(pt + 0.5) if isinstance(pt, (int, float)) else None
    line = f"{n}+ {stat}" if n is not None else stat
    opp, nba = it.get("opp"), it.get("lg") != "NBL"
    hit = lambda v: isinstance(pt, (int, float)) and v > pt
    out, score = [], 0

    # 1. form
    l10, l5 = it.get("l10") or [0, 0], it.get("l5") or [0, 0]
    if l10[1]:
        s = (f"{name} has hit {line} in every one of his last {l10[1]}" if l10[0] == l10[1]
             else f"{name} has hit {line} in {l10[0]} of his last {l10[1]}")
        s += "" if l10[0] == l10[1] else f", including all of his last {l5[1]}" if l5[1] and l5[0] == l5[1] else (f" and {l5[0]} of the last {l5[1]}" if l5[1] else "")
        out.append(s + f", averaging {_num(it.get('avg'))}.")
        score += (l10[0] / l10[1] >= 0.8) + (l5[1] and l5[0] == l5[1])

    # 2. last game, workload, back-to-back
    games = it.get("games") or []
    if games:
        g = games[0]
        mins = [x[4] for x in games if len(x) > 4 and x[4]]
        s = f"He had {_num(g[3])} last time out {'at' if g[2] == '@' else 'against'} {_the(g[1])}"
        heavy = False
        if len(g) > 4 and g[4]:
            s += f" in {round(g[4])} minutes"
            avg_min = sum(mins) / len(mins) if mins else 0
            heavy = g[4] >= 38 and g[4] >= avg_min + 5
        s += "."
        if heavy:
            s += f" That's a heavy load next to his usual {round(sum(mins) / len(mins))}, and big-minute games can take a toll on the next one."
            score -= 1
        out.append(s)
        try:
            if nba and it.get("start"):
                _, day = _local_day(it["start"])
                if (day - datetime.fromisoformat(g[0]).date()).days == 1:
                    out.append("Tonight is the second night of a back-to-back.")
                    score -= 1
        except ValueError:
            pass

    # 3. matchup: how tonight's opponent defends his position for this stat
    mu = it.get("mu")
    if mu and opp:
        r, of, pct, pos = mu["rank"], mu["of"], mu.get("pct", 0), POS.get(mu.get("pos"), "his position")
        if r <= of / 3:
            out.append(f"{_cap(_the(opp))} give up the {_ord(r)}-most {stat} to {pos} in the league "
                       f"({pct:+d}% on average), so the matchup is on his side.")
            score += 1
        elif r > of * 2 / 3:
            f = of + 1 - r
            out.append(f"{_cap(_the(opp))} allow the {_ord(f)}-fewest {stat} to {pos} ({pct:+d}% on average), "
                       f"which makes this a tougher spot.")
            score -= 1
        else:
            out.append(f"{_cap(_the(opp))} sit around the middle of the league defending {pos} on {stat}.")

    # 4. head-to-head
    h2h = [m for m in (it.get("h2h") or []) if len(m) > 3]
    if h2h and opp:
        vals = [_num(m[3]) for m in h2h]
        k = sum(1 for m in h2h if hit(m[3]))
        if len(h2h) == 1:
            out.append(f"He had {vals[0]} the last time he played {_the(opp)}{' (a hit)' if k else ''}.")
        else:
            out.append(f"His last {len(h2h)} games against {_the(opp)}: {_list(vals)}, clearing this line {k} of {len(h2h)} times.")
        score += 1 if k * 2 > len(h2h) else (-1 if k * 2 < len(h2h) else 0)

    # 5. role: a teammate out, projected minutes, blowout risk
    wo = it.get("wo")
    if wo:
        out.append(f"With {wo['n']} out, he's cleared it in {wo['hit'][0]} of {wo['hit'][1]} games without him, "
                   f"averaging {_num(wo['avg'])} compared with {_num(wo['with'])} when they play together.")
        score += 1 if wo["avg"] > wo["with"] else 0
    proj = it.get("proj")
    if proj:
        why = " ".join(proj.get("why") or [])
        d = proj["min"] - proj["l10"]
        if d >= 2:
            rs = ("with teammates out" if "teammates out" in why else "as a starter" if "starting" in why else "")
            out.append(f"He projects for about {_num(proj['min'])} minutes tonight, up from {_num(proj['l10'])} lately"
                       + (f" {rs}" if rs else "") + ".")
            score += 1
        elif d <= -2 and "blowout" not in why:
            out.append(f"He projects for about {_num(proj['min'])} minutes, down on his recent {_num(proj['l10'])}.")
            score -= 1
        if "blowout" in why:
            out.append("The spread points to a possible blowout, which could cut into his minutes late.")
            score -= 1
    pace = it.get("pace")
    if isinstance(pace, (int, float)) and abs(pace) >= 2:
        out.append(f"It shapes as a {'fast' if pace > 0 else 'slow'} game ({pace:+.0f}% pace), "
                   + ("so more chances to get there." if pace > 0 else "which takes a few chances away."))

    # 6. price
    prices = sorted((it.get("prices") or {}).values())
    if prices and it.get("price"):
        med = prices[len(prices) // 2]
        s = f"The ${it['price']:.2f} at {it.get('book')}"
        s += f" beats most bookies (around ${med:.2f})" if it["price"] >= med * 1.04 else " is in line with the market"
        if isinstance(it.get("prob"), (int, float)) and it["prob"] > 0:
            need = round(100 / it["price"])
            s += f". It needs a {need}% chance and we rate it about {round(it['prob'] * 100)}%"
            score += it["prob"] * 100 > need + 2
        out.append(s + ".")

    # 7. injury flag
    inj = it.get("inj")
    if inj in ("Q", "GTD"):
        out.append(f"He's listed {'questionable' if inj == 'Q' else 'as a game-time decision'}, so wait for confirmation he plays.")

    # 8. the lean
    if score >= 3:
        out.append(f"Form, role and price mostly point the same way, so {last} is one we like at this number.")
    elif score >= 1:
        out.append(f"On balance it leans {last}'s way, but it's not without risk.")
    else:
        out.append("The form is there, but there are enough warning signs to keep the stake small.")
    return " ".join(out)
