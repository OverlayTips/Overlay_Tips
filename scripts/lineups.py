"""NBA starting lineups + injury report for the free public page (lineups.html).

Run on its own (python scripts/lineups.py) by .github/workflows/lineups.yml, every 15 min around
tip-off and hourly otherwise. Writes OUT_DIR/lineups.json (unencrypted on purpose: it's public info).

Sources, all public and key-free, each one best effort (a failure just leaves that part out):
  games     ESPN scoreboard            -> matchups, tip-off times, game status
  lineups   NBA.com daily lineups JSON -> starting five per team, "Confirmed" or "Expected"
  injuries  NBA official injury report -> Out / Doubtful / Questionable / Probable (+ reason)
            ESPN injuries API          -> fallback / extra detail when the official report misses a player

Output:
  {"updated": TS, "sources": {name: "ok" | error},
   "games": [{"id", "start", "state": "pre"|"in"|"post", "status": "7:00 PM ET" / "Final" ...,
              "away": TEAM, "home": TEAM}],
   "players": {norm(name): {"name", "team", "status", "starter": bool, "confirmed": bool}}}   # quick lookup for other pages
  TEAM = {"abbr", "name", "confirmed": bool, "starters": [{"name", "pos"}],
          "injuries": [{"name", "status", "reason", "src"}]}
"""
import io, json, os, re, sys, unicodedata, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

OUT = os.environ.get("OUT_DIR", "lineups-data")
ET = ZoneInfo("America/New_York")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
NBA_LINEUPS = "https://stats.nba.com/js/data/leaders/00_daily_lineups_{d}.json"
NBA_INJ = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{d}_{t}.pdf"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
NBA_HDRS = {"User-Agent": UA, "Referer": "https://www.nba.com/", "Origin": "https://www.nba.com",
            "Accept": "application/json, text/plain, */*"}

# ESPN uses a few short codes; NBA.com uses the standard three letters
ESPN2NBA = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"}
TEAMS = {  # official injury report uses full names
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS"}
STATUSES = ("Out", "Doubtful", "Questionable", "Probable", "Available")
RANK = {"Out": 0, "Doubtful": 1, "Questionable": 2, "Day-To-Day": 2, "Probable": 3, "Available": 4}


def norm(name):
    """Same as player_stats.norm, so bookie names and these names match."""
    s = unicodedata.normalize("NFD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'’`]", "", s)
    s = re.sub(r"[-]", " ", s)
    return " ".join(t for t in s.split() if t not in ("jr", "sr", "ii", "iii", "iv", "v"))


def _get(url, headers=None, raw=False, timeout=20):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if raw else json.loads(data)


def _abbr(a):
    a = (a or "").upper()
    return ESPN2NBA.get(a, a)


# ---------------------------------------------------------------- games (ESPN scoreboard)
def espn_games(dates):
    games = {}
    for d in dates:
        sb = _get(f"{ESPN}/scoreboard?dates={d:%Y%m%d}")
        for ev in sb.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            side = {}
            for c in comp.get("competitors", []):
                t = c.get("team") or {}
                side[c.get("homeAway")] = {"abbr": _abbr(t.get("abbreviation")), "name": t.get("displayName") or "",
                                           "confirmed": False, "starters": [], "injuries": []}
            if "home" not in side or "away" not in side:
                continue
            st = (ev.get("status") or {}).get("type") or {}
            games[ev["id"]] = {"id": ev["id"], "start": ev.get("date"), "state": st.get("state") or "pre",
                               "status": st.get("shortDetail") or "", "away": side["away"], "home": side["home"]}
    return games


# ---------------------------------------------------------------- lineups (NBA.com)
def nba_lineups(dates):
    """{team abbr: {"confirmed": bool, "starters": [...], "inactive": [names]}}"""
    out, got = {}, 0
    for d in dates:
        try:
            js = _get(NBA_LINEUPS.format(d=f"{d:%Y%m%d}"), NBA_HDRS)
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):   # file isn't up yet for that date
                continue
            raise
        got += 1
        for g in js.get("games", []):
            for k in ("homeTeam", "awayTeam"):
                t = g.get(k) or {}
                ab = _abbr(t.get("teamAbbreviation"))
                if not ab:
                    continue
                starters, inactive, conf = [], [], []
                for p in t.get("players", []):
                    nm = p.get("playerName") or f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
                    if (p.get("rosterStatus") or "").lower() == "inactive":
                        inactive.append(nm)
                    if p.get("position"):
                        starters.append({"name": nm, "pos": p["position"]})
                        conf.append((p.get("lineupStatus") or "").lower() == "confirmed")
                if starters and ab not in out:
                    out[ab] = {"confirmed": bool(conf) and all(conf), "starters": starters, "inactive": inactive}
    if not got:
        raise RuntimeError("no lineup file for these dates yet")
    return out


# ---------------------------------------------------------------- injuries: NBA official report (PDF)
def _report_urls(now_et):
    """Newest first. The NBA posts a new report through the day; file names have changed formats over
    the years (…_05PM.pdf, …_05_30PM.pdf), so try both, walking back in 15 min steps."""
    t = now_et.replace(minute=now_et.minute // 15 * 15, second=0, microsecond=0)
    for _ in range(4 * 10):
        d = f"{t:%Y-%m-%d}"
        h = t.strftime("%I").lstrip("0").zfill(2)
        ap = t.strftime("%p")
        yield NBA_INJ.format(d=d, t=f"{h}_{t:%M}{ap}")
        if t.minute == 0:
            yield NBA_INJ.format(d=d, t=f"{h}{ap}")
        t -= timedelta(minutes=15)


_NAME = r"(?P<name>[A-Z][A-Za-z'.\-]+(?: [A-Z][A-Za-z'.\-]+)*(?: (?:Jr\.|Sr\.|II|III|IV))?, [A-Z][A-Za-z'.\-]+(?: [A-Za-z'.\-]+)*)"
_ROW = re.compile(_NAME + r" (?P<status>" + "|".join(STATUSES) + r")\b ?(?P<reason>.*)$")


def parse_injury_report(text):
    """{team abbr: [{"name", "status", "reason"}]} from the official report's extracted text."""
    teams_re = re.compile("|".join(re.escape(n) for n in sorted(TEAMS, key=len, reverse=True)))
    out, team = {}, None
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line or line.startswith(("Injury Report:", "Page ", "Game Date")):
            continue
        tm = None
        for m in teams_re.finditer(line):
            tm = m
        if tm:
            team = TEAMS[tm.group(0)]
            rest = line[tm.end():].strip()
        else:
            rest = line
        if not team:
            continue
        m = _ROW.search(rest)
        if not m:
            continue
        last, first = [s.strip() for s in m.group("name").split(",", 1)]
        sfx = re.search(r" (Jr\.|Sr\.|II|III|IV)$", last)
        if sfx:
            last = last[:sfx.start()]
        name = f"{first} {last}" + (f" {sfx.group(1)}" if sfx else "")
        out.setdefault(team, []).append({"name": name, "status": m.group("status"),
                                         "reason": m.group("reason").strip()[:120]})
    return out


def nba_injuries(now):
    import pdfplumber   # installed by the workflow
    now_et = now.astimezone(ET)
    for url in _report_urls(now_et):
        try:
            pdf = _get(url, {"User-Agent": UA}, raw=True, timeout=15)
        except urllib.error.HTTPError:
            continue
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            text = "\n".join((p.extract_text() or "") for p in doc.pages)
        inj = parse_injury_report(text)
        if inj:
            return inj, url.rsplit("/", 1)[-1]
    raise RuntimeError("no official injury report found in the last 10 hours")


# ---------------------------------------------------------------- injuries: ESPN (fallback)
def espn_injuries():
    js = _get(f"{ESPN}/injuries")
    out = {}
    for t in js.get("injuries", []):
        ab = TEAMS.get(t.get("displayName") or "")
        for i in t.get("injuries", []):
            ath = i.get("athlete") or {}
            ab2 = ab or _abbr(((ath.get("team") or {}).get("abbreviation")))
            if not ab2:
                continue
            st = i.get("status") or ""
            st = "Day-To-Day" if st.lower().replace(" ", "-") == "day-to-day" else st
            det = i.get("details") or {}
            reason = " ".join(x for x in (det.get("side"), det.get("type")) if x and x != "Not Specified") \
                or i.get("shortComment") or ""
            out.setdefault(ab2, []).append({"name": ath.get("displayName") or "", "status": st,
                                            "reason": reason[:120], "ret": det.get("returnDate")})
    return out


# ---------------------------------------------------------------- build
def build(now):
    now_et = now.astimezone(ET)
    dates = [now_et.date(), now_et.date() + timedelta(days=1)]
    if now_et.hour < 4:   # late West Coast games from "yesterday" can still be on
        dates.insert(0, now_et.date() - timedelta(days=1))
    sources, games, lu, off, esp = {}, {}, {}, {}, {}
    for name, fn in (("games", lambda: espn_games(dates)), ("lineups", lambda: nba_lineups(dates)),
                     ("injury_report", lambda: nba_injuries(now)), ("espn_injuries", espn_injuries)):
        try:
            res = fn()
            if name == "games":
                games = res
            elif name == "lineups":
                lu = res
            elif name == "injury_report":
                off, sources["injury_report_file"] = res
            else:
                esp = res
            sources[name] = "ok"
        except Exception as e:   # best effort, keep going
            sources[name] = f"{type(e).__name__}: {e}"[:200]
        print(f"{name}: {sources[name]}")

    # drop finished games from before today and anything more than ~36h out
    keep = {}
    for g in games.values():
        try:
            st = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
        except Exception:
            continue
        if st > now + timedelta(hours=36) or (g["state"] == "post" and st < now - timedelta(hours=6)):
            continue
        keep[g["id"]] = g

    players = {}
    for g in keep.values():
        for side in ("away", "home"):
            t = g[side]
            ab = t["abbr"]
            l = lu.get(ab) or {}
            t["starters"] = l.get("starters", [])
            t["confirmed"] = l.get("confirmed", False)
            inj, seen = [], set()
            for i in off.get(ab, []):
                inj.append({**i, "src": "NBA"})
                seen.add(norm(i["name"]))
            for i in esp.get(ab, []):
                if norm(i["name"]) not in seen:
                    inj.append({**{k: v for k, v in i.items() if v}, "src": "ESPN"})
                    seen.add(norm(i["name"]))
            for nm in l.get("inactive", []):
                if norm(nm) not in seen:
                    inj.append({"name": nm, "status": "Out", "reason": "Inactive", "src": "NBA"})
                    seen.add(norm(nm))
            inj.sort(key=lambda i: (RANK.get(i["status"], 5), i["name"]))
            t["injuries"] = inj
            for i in inj:
                players[norm(i["name"])] = {"name": i["name"], "team": ab, "status": i["status"],
                                            "starter": False, "confirmed": False}
            for s in t["starters"]:
                p = players.setdefault(norm(s["name"]), {"name": s["name"], "team": ab, "status": ""})
                p["starter"], p["confirmed"] = True, t["confirmed"]
    gl = sorted(keep.values(), key=lambda g: g["start"])
    return {"updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": sources, "games": gl, "players": players}


def main():
    now = datetime.now(timezone.utc)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "lineups.json")
    res = build(now)
    if not res["games"] and os.path.exists(path):
        print("no games came back, keeping the previous file")
        return
    with open(path, "w") as f:
        json.dump(res, f, separators=(",", ":"))
    n_conf = sum(g[s]["confirmed"] for g in res["games"] for s in ("away", "home"))
    n_inj = sum(len(g[s]["injuries"]) for g in res["games"] for s in ("away", "home"))
    print(f"lineups.json: {len(res['games'])} games, {n_conf} confirmed lineups, {n_inj} injury entries")
    if "--debug" in sys.argv:
        print(json.dumps(res["games"][:1], indent=1)[:3000])


if __name__ == "__main__":
    main()
