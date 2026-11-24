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


NFL = {"ARI": "Cardinals", "ATL": "Falcons", "BAL": "Ravens", "BUF": "Bills", "CAR": "Panthers", "CHI": "Bears",
       "CIN": "Bengals", "CLE": "Browns", "DAL": "Cowboys", "DEN": "Broncos", "DET": "Lions", "GB": "Packers",
       "HOU": "Texans", "IND": "Colts", "JAX": "Jaguars", "KC": "Chiefs", "LV": "Raiders", "LAC": "Chargers",
       "LAR": "Rams", "MIA": "Dolphins", "MIN": "Vikings", "NE": "Patriots", "NO": "Saints", "NYG": "Giants",
       "NYJ": "Jets", "PHI": "Eagles", "PIT": "Steelers", "SF": "49ers", "SEA": "Seahawks", "TB": "Buccaneers",
       "TEN": "Titans", "WSH": "Commanders", "WAS": "Commanders"}
_LG = {"nfl": False}   # set per write-up: NFL abbreviations clash with NBA ones (MIA, DAL, DEN ...)


def _the(x):
    """'the Cavaliers' for NBA nicknames ('the Chiefs' for NFL), plain name otherwise (NBL names come in full)."""
    names = NFL if _LG["nfl"] else NBA
    return f"the {names[x]}" if x in names else (x or "them")


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
    """The case for one prop. it: a Hot stats item (or prop_item() below); it["side"] = "Under" flips the logic."""
    name, last = it.get("player", "He"), _last(it.get("player", "He"))
    pt, stat = it.get("point"), _stat(it.get("stat", ""))
    under = str(it.get("side", "")).lower() == "under"
    sgn = -1 if under else 1   # a signal that helps the Over hurts the Under
    num = isinstance(pt, (int, float))
    opp, nba = it.get("opp"), it.get("lg") not in ("NBL", "NFL")
    _LG["nfl"] = it.get("lg") == "NFL"
    hit = lambda v: num and (v < pt if under else v > pt)
    out, score = [], 0
    secs, sec = {}, "Form"   # labelled paragraphs: Form, Matchup, Role, Game, Price, Verdict
    def add(t):
        secs.setdefault(sec, []).append(t)

    # 1. form
    l10, l5 = it.get("l10") or [0, 0], it.get("l5") or [0, 0]
    if l10[1]:
        did = f"stayed under {_num(pt)} {stat}" if under else f"hit {int(pt + 0.5)}+ {stat}" if num else f"hit this line"
        s = (f"{name} has {did} in every one of his last {l10[1]}" if l10[0] == l10[1]
             else f"{name} has {did} in {l10[0]} of his last {l10[1]}")
        s += "" if l10[0] == l10[1] else f", including all of his last {l5[1]}" if l5[1] and l5[0] == l5[1] else (f" and {l5[0]} of the last {l5[1]}" if l5[1] and l5[1] < l10[1] else "")
        add(s + f", averaging {_num(it.get('avg'))}.")
        score += (l10[0] / l10[1] >= 0.8) + bool(l5[1] and l5[0] == l5[1]) - (l10[0] / l10[1] < 0.5)
    weak = bool(l10[1]) and l10[0] / l10[1] < 0.5
    mv = it.get("moved")   # recently traded: the numbers above are new-team games only (hot.team_games)
    if mv and mv.get("from") and mv.get("g"):
        add(f"He's played {mv['g']} games since joining from {_the(mv['from'])}, so these numbers only count "
            f"games with his new team.")
    rl = it.get("role")   # role change on the same team: the numbers above are new-role games only (hot.role_window)
    if rl and rl.get("g"):
        up = rl["to"] > rl["from"]
        add(f"His role's changed: he's playing about {_num(round(rl['to']))} minutes a game over his last {rl['g']}, "
            f"{'up' if up else 'down'} from {_num(round(rl['from']))} before that, so these numbers only count games "
            f"in the new role.")

    # 2. last game, workload, back-to-back (tired legs help an Under)
    games = it.get("games") or []
    if games:
        g = games[0]
        mins = [x[4] for x in games if len(x) > 4 and x[4]]
        s = f"He had {_num(g[3])} last time out {'at' if g[2] == '@' else 'against'} {_the(g[1])}"
        heavy = False
        if len(g) > 4 and g[4]:
            s += f" in {round(g[4])} minutes"
            heavy = mins and g[4] >= 38 and g[4] >= sum(mins) / len(mins) + 5
        s += "."
        if heavy:
            s += f" That's a heavy load next to his usual {round(sum(mins) / len(mins))}, and big-minute games can take a toll on the next one."
            score -= sgn
        add(s)
        try:
            if nba and it.get("start"):
                _, day = _local_day(it["start"])
                if (day - datetime.fromisoformat(g[0]).date()).days == 1:
                    add("This is the second night of a back-to-back" + (", which suits the Under." if under else "."))
                    score -= sgn
        except ValueError:
            pass

    sec = "Matchup"
    # 3. matchup: how tonight's opponent defends his position for this stat
    mu = it.get("mu")
    if mu and opp:
        r, of, pct, pos = mu["rank"], mu["of"], mu.get("pct", 0), POS.get(mu.get("pos"), "his position")
        if r <= of / 3:
            add(f"{_cap(_the(opp))} give up the {_ord(r)}-most {stat} to {pos} in the league ({pct:+d}% on average), "
                       + ("which works against the Under." if under else "so the matchup is on his side."))
            score += sgn
        elif r > of * 2 / 3:
            add(f"{_cap(_the(opp))} allow the {_ord(of + 1 - r)}-fewest {stat} to {pos} ({pct:+d}% on average), "
                       + ("so the matchup is on the Under's side." if under else "which makes this a tougher spot."))
            score -= sgn
        else:
            add(f"{_cap(_the(opp))} sit around the middle of the league defending {pos} on {stat}.")

    sec = "Role"
    # 3b. NFL: workload trend, what the defence gives up to his position, game script
    nf = it.get("nfl") or {}
    u = nf.get("use")
    if u and u["season"]:
        d = u["l3"] - u["season"]
        s = f"He's averaging {_num(u['l3'])} {u['what']} over his last 3 games"
        if abs(d) >= max(1, 0.15 * u["season"]):
            s += f", {'up' if d > 0 else 'down'} from {_num(u['season'])} for the season"
            score += sgn if d > 0 else -sgn
        else:
            s += f", right on his season average of {_num(u['season'])}"
        if u.get("catch") is not None:
            s += f", and he catches {u['catch']}% of his targets"
        add(s + ".")
    sec = "Matchup"
    df = nf.get("def")
    if df:
        pos = {"QB": "quarterbacks", "RB": "running backs", "WR": "receivers", "TE": "tight ends"}.get(df["pos"], "his position")
        r, of = df["rank"], df["of"]
        if r <= of / 3:
            add(f"{_cap(_the(df['opp']))} give up the {_ord(r)}-most {stat} to {pos} ({_num(df['avg'])} a game, {df['pct']:+d}% on the league average), "
                       + ("which works against the Under." if under else "a good matchup for him."))
            score += sgn
        elif r > of * 2 / 3:
            add(f"{_cap(_the(df['opp']))} allow the {_ord(of + 1 - r)}-fewest {stat} to {pos} ({_num(df['avg'])} a game, {df['pct']:+d}%), "
                       + ("which suits the Under." if under else "so it's a tougher matchup."))
            score -= sgn
        else:
            add(f"{_cap(_the(df['opp']))} are around the league average against {pos} on {stat} ({_num(df['avg'])} a game).")
    sec = "Game"
    gm = nf.get("gm")
    if gm:
        sp, tt = gm["spread"], gm["tt"]
        fav = sp < 0
        s = (f"His team is {'favoured by ' + _num(-sp) if fav else _num(sp) + '-point underdogs' if sp > 0 else 'a pick-em'} "
             f"with a total of {_num(gm['total'])}, so the market has them scoring about {_num(tt)}")
        passing = any(w in stat for w in ("pass", "complet", "recept", "receiving"))
        if sp >= 3.5 and passing:
            s += ". Playing from behind usually means more throws" + (", a worry for the Under." if under else ", which helps.")
            score += sgn
        elif sp <= -3.5 and "rush" in stat:
            s += ". Protecting a lead usually means more runs late" + (", a worry for the Under." if under else ", which helps.")
            score += sgn
        elif sp <= -6.5 and passing:
            s += ". A big favourite can take its foot off the passing game late" + (", which suits the Under." if under else ".")
            score -= sgn
        elif tt >= 26:
            s += ", a high-scoring script" + (" that works against the Under." if under else " with plenty of chances.")
            score += sgn
        elif tt <= 18:
            s += ", a low-scoring script" + (" that suits the Under." if under else " that limits his chances.")
            score -= sgn
        add(s + ("" if s.endswith(".") else "."))

    sec = "Matchup"
    # 4. head-to-head
    h2h = [m for m in (it.get("h2h") or []) if len(m) > 3]
    if h2h and opp:
        vals = [_num(m[3]) for m in h2h]
        k = sum(1 for m in h2h if hit(m[3]))
        if len(h2h) == 1:
            add(f"He had {vals[0]} the last time he played {_the(opp)}{' (a hit)' if k else ''}.")
        else:
            add(f"His last {len(h2h)} games against {_the(opp)}: {_list(vals)}, landing this bet {k} of {len(h2h)} times.")
        score += 1 if k * 2 > len(h2h) else (-1 if k * 2 < len(h2h) else 0)

    sec = "Role"
    # 5. role: a teammate out, projected minutes, blowout risk
    wo = it.get("wo")
    if wo:
        add(f"With {wo['n']} out, he's averaging {_num(wo['avg'])} without him compared with {_num(wo['with'])} "
                   f"when they play together ({wo['hit'][0]} of {wo['hit'][1]} games landing).")
        score += sgn if wo["avg"] > wo["with"] else -sgn if wo["avg"] < wo["with"] else 0
    proj = it.get("proj")
    if proj:
        why = " ".join(proj.get("why") or [])
        d = proj["min"] - proj["l10"]
        if d >= 2:
            rs = ("with teammates out" if "teammates out" in why else "as a starter" if "starting" in why else "")
            add(f"He projects for about {_num(proj['min'])} minutes, up from {_num(proj['l10'])} lately"
                       + (f" {rs}" if rs else "") + (", a worry for the Under." if under else "."))
            score += sgn
        elif d <= -2 and "blowout" not in why:
            add(f"He projects for about {_num(proj['min'])} minutes, down on his recent {_num(proj['l10'])}.")
            score -= sgn
        if "blowout" in why:
            add("The spread points to a possible blowout, which could cut into his minutes late"
                       + (" and help the Under." if under else "."))
            score -= sgn
    sec = "Game"
    pace = it.get("pace")
    if isinstance(pace, (int, float)) and abs(pace) >= 2:
        fast = pace > 0
        add(f"It shapes as a {'fast' if fast else 'slow'} game ({pace:+.0f}% pace), "
                   + ("so more chances to get there." if fast and not under else "which works against the Under." if fast
                      else "which suits the Under." if under else "which takes a few chances away."))

    sec = "Price"
    # 6. price
    pr = it.get("price")
    prices = sorted((it.get("prices") or {}).values())
    if pr:
        s = f"The ${pr:.2f} at {it.get('book')}"
        if isinstance(it.get("fair"), (int, float)) and it["fair"] > 1:
            src = "Pinnacle's line" if it.get("fair_src") == "pin" else "the bookies' consensus"
            s += f" is {it.get('edge') or round((pr / it['fair'] - 1) * 100, 1)}% above the fair price of ${it['fair']:.2f} ({src} with the margin taken off)"
            score += 1
        elif prices:
            med = prices[len(prices) // 2]
            s += f" beats most bookies (around ${med:.2f})" if pr >= med * 1.04 else " is in line with the market"
        if isinstance(it.get("prob"), (int, float)) and it["prob"] > 0:
            need = round(100 / pr)
            s += f". It needs a {need}% chance and we rate it about {round(it['prob'] * 100)}%"
            score += it["prob"] * 100 > need + 2
        add(s + ".")

    sec = "Role"
    # 7. injury flag
    inj = it.get("inj")
    if inj in ("Q", "GTD"):
        add(f"He's listed {'questionable' if inj == 'Q' else 'as a game-time decision'}, so wait for confirmation he plays.")

    sec = "Verdict"
    # 8. the lean
    if weak:
        add(f"His recent form doesn't back it up ({l10[0]} of {l10[1]}), so this one is mostly about the price. Keep the stake small.")
    elif score >= 3 and not pr:
        add(f"Form, role and matchup mostly point the same way, so the {last} {'Under' if under else 'Over'} looks the right side of this line.")
    elif score >= 3:
        add(f"Form, role and price mostly point the same way, so the {last} {'Under' if under else 'Over'} is one we like at this number.")
    elif score >= 1:
        add(f"On balance it leans the {'Under' if under else 'Over'}, but it's not without risk.")
    elif not pr:
        add("The signals are mixed, so it comes down to the price.")
    else:
        add("There's value in the price, but enough warning signs to keep the stake small.")
    order = [("Form", "📈"), ("Matchup", "🛡"), ("Role", "👤"), ("Game", "🎯"), ("Price", "💰"), ("Verdict", "✅")]
    return "\n\n".join(f"{e} {k}: " + " ".join(secs[k]) for k, e in order if secs.get(k))


# ---------------- overlays (picks.py) ----------------
def prop_item(o, stats, sport_key, ctx=None):
    """A Hot-stats-shaped item for an overlay on an NBA/NBL player prop, from the raw box scores, or None."""
    import hot
    mk, desc, side, pt = o.get("mk"), o.get("desc"), o.get("side"), o.get("point")
    nfl = sport_key == "americanfootball_nfl"
    ST = hot.NFL_STAT if nfl else hot.STAT
    if mk not in ST or not desc or side not in ("Over", "Under") or not isinstance(pt, (int, float)):
        return None
    players, by, teams = stats.get("players", {}), stats.get("by", {}), stats.get("teams", {})
    p = players.get(by.get(desc, ""))
    if not p:
        return None
    label, f = ST[mk]
    names = [x.strip() for x in str(o.get("game", "")).replace(" @ ", " v ").split(" v ")]
    abbrs = [teams.get(n) for n in names]
    gs, moved = hot.team_games(p, [g for g in p.get("g", []) if g[3] > 0], *(abbrs + [None, None])[:2], sport_key)
    if len(gs) < 3:
        return None
    under = side == "Under"
    hit = lambda xs: sum(1 for g in xs if (f(g) < pt if under else f(g) > pt))
    l10, role = hot.role_window(gs, sport_key)
    l5 = gs[:5]
    opp = next((a for a in abbrs if a and a != p.get("team")), None)
    meet = [g for g in gs if opp and g[1] == opp] + [g for g in p.get("p", []) if opp and g[1] == opp and g[3] > 0]
    it = {"player": p.get("n") or desc, "team": p.get("team"), "opp": opp,
            "lg": "NFL" if nfl else "NBL" if sport_key == "basketball_nbl" else "NBA", "start": o.get("start"),
            "stat": label, "point": pt, "side": side, "price": o.get("price"), "book": o.get("book"),
            "fair": o.get("fair"), "fair_src": o.get("fair_src"), "edge": o.get("edge_pct"), "inj": o.get("inj"),
            "l10": [hit(l10), len(l10)], "l5": [hit(l5), len(l5)],
            "avg": round(sum(f(g) for g in l10) / len(l10), 1),
            "games": [[g[0], g[1], g[2], f(g), None if nfl else round(g[3])] for g in l10],
            "h2h": [[g[0], g[1], g[2], f(g)] for g in meet[:3]]}
    if moved:
        it["moved"] = moved
    if role:
        it["role"] = role
    if nfl:
        gm = o.get("gm")
        c = hot.nfl_ctx(players, p, gs, mk, f, opp, gm, o.get("_dcache"))
        if c:
            it["nfl"] = c
    elif sport_key == "basketball_nba" and ctx:   # matchup v his position, pace, usage (matchups.py)
        try:
            import matchups as mx
            pk = by.get(desc, "")
            pos = {k: v[3] for k, v in ((stats.get("ids") or {}).get("map") or {}).items() if len(v) > 3}.get(pk)
            mu = mx.matchup(ctx, opp, pos, mk)
            pc = mx.pace(ctx, p.get("team"), opp)
            usg = (ctx.get("usg") or {}).get(str(p.get("id")))
            if mu:
                it["mu"] = {k: mu[k] for k in ("pos", "rank", "of", "pct")}
            if pc is not None:
                it["pace"] = pc
            if usg:
                it["usg"] = usg
        except Exception:
            pass
    return it


def all_cases(prop_events, stats_by_sport, ctx=None, games=()):
    """Write-up for every main player prop line, both sides, for the odds page's "Why":
    {"<event id>|<market>|<player>|<Over/Under>|<point>": text}. No price sentence (the page adds its own,
    for whichever bookie you pick)."""
    import hot, picks
    lines = {ev["id"]: hot._nfl_lines(ev) for ev in games or () if ev.get("sport_key") == "americanfootball_nfl"}
    out, dcache = {}, {}
    for ev in prop_events:
        sk = ev.get("sport_key")
        st = stats_by_sport.get(sk)
        if not st or not st.get("players"):
            continue
        spr, tot = lines.get(ev["id"], ({}, None))
        teams = st.get("teams", {})
        for mk, desc, sides, main in picks._groups(ev):
            if not desc or mk.endswith("_alternate"):
                continue
            pt = main.get("Over", main.get("Under"))
            if not isinstance(pt, (int, float)):
                continue
            for side in ("Over", "Under"):
                o = {"mk": mk, "desc": desc, "side": side, "point": pt, "game": f"{ev['away_team']} @ {ev['home_team']}",
                     "start": ev["commence_time"], "_dcache": dcache}
                if sk == "americanfootball_nfl":
                    p = (st.get("players") or {}).get((st.get("by") or {}).get(desc, ""))
                    me = next((n for n in (ev["home_team"], ev["away_team"]) if p and teams.get(n) == p.get("team")), None)
                    if me in spr and tot:
                        o["gm"] = (spr[me], tot)
                try:
                    it = prop_item(o, st, sk, ctx if sk == "basketball_nba" else None)
                except Exception:
                    it = None
                if it:
                    out[f"{ev['id']}|{mk}|{desc}|{side}|{pt:g}"] = writeup(it)
    return out


def market_writeup(o):
    """Short case for a game-market overlay (head-to-head, line, total) in any sport: price v fair, books."""
    src = "Pinnacle's line" if o.get("fair_src") == "pin" else "the bookies' consensus"
    s = (f"{o['pick']} ({o.get('market')}) is ${o['price']:.2f} at {o['book']}, {o['edge_pct']}% above the fair price of "
         f"${o['fair']:.2f}, which comes from {src} with the margin taken off.")
    if o.get("books"):
        s += f" {o['books']} bookies are pricing it" + (", so the fair price is well supported." if o["books"] >= 8 else ".")
    if o.get("market") == "H2H" and o["fair"] >= 3:
        s += f" It's still an outsider: the fair price puts its chance at about {round(100 / o['fair'])}%, so keep the stake small."
    elif o.get("books", 0) < 4:
        s += " Not many bookies price it, so treat the fair price with some caution."
    if o.get("inj") in ("Q", "GTD"):
        s += " He's listed questionable, so wait for confirmation he plays."
    if o.get("desc"):
        s += f" We don't have enough of {o['desc']}'s games logged yet to judge his form, so this one is on price alone."
    return s


def overlay_writeup(o, stats_by_sport):
    """The case for an overlay: the full prop write-up for NBA/NBL player props we have box scores for, else the short one."""
    sk = o.get("sport_key")
    if o.get("desc") and sk in stats_by_sport:
        try:
            it = prop_item(o, stats_by_sport[sk] or {}, sk)
            if it:
                o["form"] = it["l10"]   # [games he landed this side, games] - value alerts skip bets his stats don't back
                return writeup(it)
        except Exception:
            pass
    return market_writeup(o)
