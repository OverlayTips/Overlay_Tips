"""Posts tracker alerts and the daily results into the Overlay Tips Discord, through the bot.

Channels are found by name (the layout scripts/discord_setup.py builds), so nothing breaks if ids change.
Routing mirrors the app alerts (value_alerts.app_push): props -> Daily Card channels, game bets / racing value /
arbs -> VIP channels, bet of the day + racing top picks -> FREE channels. Never raises: Discord being down or
the secrets missing just skips the post.

Env: DISCORD_BOT_TOKEN, DISCORD_GUILD_ID.
"""
import json, os, time, urllib.error, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

API = "https://discord.com/api/v10"
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DEVIL_TOKEN = os.environ.get("DEVIL_BOT_TOKEN", "").strip()   # optional 2nd bot: devil's advocate threads post as it
GUILD = os.environ.get("DISCORD_GUILD_ID", "").strip()
MEL = ZoneInfo("Australia/Melbourne")
FOOT = "-# Price signals, not sure things. 18+ · Gamble responsibly · 1800 858 858"
_chans = None


def enabled():
    return bool(TOKEN and GUILD)


def _call(method, path, body=None, tries=5, token=None):
    """One Discord API call. On a rate limit (429) waits as long as Discord says and tries again."""
    for i in range(tries):
        req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bot {token or TOKEN}", "Content-Type": "application/json",
                                              "User-Agent": "DiscordBot (https://overlay.tips, 1.0)"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == tries - 1:
                raise
            try:
                wait = float(json.loads(e.read() or b"{}").get("retry_after") or 0)
            except Exception:
                wait = 0
            time.sleep(min(max(wait, float(e.headers.get("Retry-After") or 1)), 30) + 0.3)


def _channel(name):
    global _chans
    if _chans is None:
        _chans = {c["name"]: c["id"] for c in _call("GET", f"/guilds/{GUILD}/channels") if c.get("type") == 0}
    return _chans.get(name)


def channel_for(cat, min_tier):
    if cat == "botd":
        return "bet-of-the-day"
    if cat == "racing":
        return "racing-top-picks" if min_tier == "founder" else "racing-value"
    if cat == "arbs":
        return "arbs-steamers"
    if cat in ("nba", "nbl", "nfl"):
        return f"{cat}-props"
    return "other-props" if min_tier == "core" else "game-bets"


def send(channel, title, lines, url=None, foot=FOOT):
    """Post one message. Returns the posted message (truthy) if it went, else False."""
    if not enabled():
        return False
    try:
        cid = _channel(channel)
        if not cid:
            print(f"::warning::discord: no #{channel} channel (run the Discord setup workflow)")
            return False
        body = "\n".join(l for l in lines if l is not None)
        head, tail = f"**{title}**\n", (f"\n<{url}>" if url else "") + (f"\n{foot}" if foot else "")
        room = 1990 - len(head) - len(tail)
        if len(body) > room:   # Discord caps a message at 2000: cut at a line break so the link + footer always fit
            cut = body[:room - 2]
            body = (cut[:cut.rfind("\n")] if "\n" in cut else cut).rstrip() + "\n…"
        return _call("POST", f"/channels/{cid}/messages", {"content": head + body + tail, "allowed_mentions": {"parse": []}}) or True
    except Exception as e:
        print(f"::warning::discord post to #{channel} failed: {type(e).__name__} {e}")
        return False


def posted(channel, needle, limit=100):
    """True if one of the channel's last `limit` messages contains `needle` (to avoid double posts)."""
    if not enabled():
        return False
    try:
        cid = _channel(channel)
        return bool(cid) and any(needle in (m.get("content") or "")
                                 for m in _call("GET", f"/channels/{cid}/messages?limit={limit}") or [])
    except Exception as e:
        print(f"::warning::discord: couldn't read #{channel}: {type(e).__name__} {e}")
        return False


def open_thread(msg, name, lines):
    """Start a thread under a posted message and post `lines` in it (devil's advocate). Never raises."""
    if not (isinstance(msg, dict) and msg.get("id")):
        return False
    try:
        th = _call("POST", f"/channels/{msg['channel_id']}/messages/{msg['id']}/threads",
                   {"name": name[:100], "auto_archive_duration": 1440})
        body = {"content": "\n".join(lines)[:1990], "allowed_mentions": {"parse": []}}
        try:
            _call("POST", f"/channels/{th['id']}/messages", body, token=DEVIL_TOKEN or None)
        except Exception as e:
            if not DEVIL_TOKEN:
                raise
            print(f"::notice::devil bot couldn't post ({e}), posting as the main bot")
            _call("POST", f"/channels/{th['id']}/messages", body)
        return True
    except Exception as e:
        print(f"::warning::discord thread failed: {type(e).__name__} {e}")
        return False


def alert(cat, title, lines, url, min_tier, thread=None):
    """thread: optional (name, lines) posted as a thread under each copy of the alert (devil's advocate)."""
    def go(ch):
        m = send(ch, title, lines, url)
        if m and thread:
            open_thread(m, *thread)
        return m
    if min_tier == "founder" and cat not in ("botd", "racing"):   # best-backed props: founders' channel + the usual one
        go("founder-picks")
        return go(channel_for(cat, "core"))
    return go(channel_for(cat, min_tier))


def _day(units, clv, day):
    legs = []
    for i in range(1, len(units or [])):
        t, cum, sel, res = units[i][:4]
        if datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(MEL).date().isoformat() == day and res in ("win", "loss"):
            legs.append((sel, round(cum - units[i - 1][1], 2), res))
    cl = [x[2] for x in (clv or []) if datetime.fromisoformat(x[0].replace("Z", "+00:00")).astimezone(MEL).date().isoformat() == day]
    return legs, cl


def daily_results(record, now):
    """Yesterday's results for both systems into #results. The caller makes sure it runs once a day."""
    day = (now.astimezone(MEL).date() - timedelta(days=1)).isoformat()
    fmt = lambda u: f"{'+' if u >= 0 else '−'}{abs(u):.2f}U"
    lines, any_ = [], False
    mem = record.get("members") or {}
    for name, units, clv, tot in (("💎 VIP system", record.get("units"), record.get("clv_days"), record.get("periods", {}).get("all")),
                                  ("⭐ Founding Members & Daily Card", mem.get("units"), mem.get("clv_days"), (mem.get("periods") or {}).get("all"))):
        legs, cl = _day(units, clv, day)
        if not legs:
            continue
        any_ = True
        u, w = sum(l[1] for l in legs), sum(l[2] == "win" for l in legs)
        lines += ["", f"**{name}: {fmt(u)}** · {w}/{len(legs)} won" + (f" · {sum(cl) / len(cl):+.1f}% avg CLV" if cl else "")]
        lines += [f"{'✅' if r == 'win' else '❌'} {s} {fmt(p)}" for s, p, r in legs[:10]]
        if len(legs) > 10:
            lines.append(f"+ {len(legs) - 10} more on the record")
        if tot and tot.get("w", 0) + tot.get("l", 0):
            lines.append(f"Total so far: **{fmt(tot['units'])}** ({tot['w']}-{tot['l']})")
    if not any_:
        print(f"discord results: no graded bets on {day}, nothing to post")
        return False
    d = datetime.fromisoformat(day)
    ok = send("results", f"📊 Results · {d.strftime('%a')} {d.day} {d.strftime('%b')}", lines,
                "https://overlay.tips/record.html")
    print(f"discord results: {day} {'posted to #results' if ok else 'NOT posted'}")
    return ok


LEAGUES = (("basketball_nba", "🏀 NBA"), ("americanfootball_nfl", "🏈 NFL"), ("basketball_nbl", "🇦🇺 NBL"))


def league_summary(bets, now):
    """Yesterday's units by league into #results: NBA, NFL and NBL, each only if it had bets that day (Melbourne
    day of the start). Waits until that day's bets have all settled, or 6pm, so the numbers don't change after.
    The caller makes sure it runs once a day."""
    local = now.astimezone(MEL)
    day = local.date() - timedelta(days=1)
    fmt = lambda u: f"{'+' if u >= 0 else '−'}{abs(u):.2f}U"
    lines, tot, pend = [], [0.0, 0.0, 0, 0], 0   # units, staked, wins, losses
    for key, label in LEAGUES:
        bs = [b for b in bets if b.get("sport_key") == key and b.get("start")
              and datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(MEL).date() == day]
        if not bs:
            continue   # no action in that league that day: leave it out
        gr = [b for b in bs if b.get("result") in ("win", "loss")]
        pend += sum(b.get("result") is None for b in bs)
        if not gr:
            continue
        u = sum(((b["price"] - 1) if b["result"] == "win" else -1) * (b.get("stake") or 1) for b in gr)
        st = sum(b.get("stake") or 1 for b in gr)
        w = sum(b["result"] == "win" for b in gr)
        v = sum(b.get("result") == "void" for b in bs)
        best = max(gr, key=lambda b: ((b["price"] - 1) if b["result"] == "win" else -1) * (b.get("stake") or 1))
        lines.append(f"{'🟢' if u > 0.005 else '🔴' if u < -0.005 else '⚪'} **{label}: {fmt(u)}** · {w}-{len(gr) - w}"
                     + (f" ({v} void)" if v else "") + f" · {st:g}U staked · ROI {100 * u / st:+.0f}%")
        if best["result"] == "win":
            lines.append(f"-# Best: {best.get('sel', '')} @ ${best['price']:.2f} {fmt((best['price'] - 1) * (best.get('stake') or 1))}")
        tot = [tot[0] + u, tot[1] + st, tot[2] + w, tot[3] + len(gr) - w]
    if pend and local.hour < 18:
        print(f"discord league summary: {pend} bets from {day} still settling, waiting")
        return False
    if not lines:
        print(f"discord league summary: no graded NBA/NFL/NBL bets on {day}, nothing to post")
        return False
    if sum(l.startswith(("🟢", "🔴", "⚪")) for l in lines) > 1:
        lines += ["", f"**Total: {fmt(tot[0])}** · {tot[2]}-{tot[3]} · {tot[1]:g}U staked · ROI {100 * tot[0] / tot[1]:+.0f}%"]
    ok = send("results", f"🧾 Units by league · {day.strftime('%a')} {day.day} {day.strftime('%b')}", lines,
              "https://overlay.tips/record.html")
    print(f"discord league summary: {day} {'posted to #results' if ok else 'NOT posted'}")
    return ok


def weekly_recap(record, now):
    """Monday: last week (Mon-Sun, Melbourne) for both systems into #results, with the best and worst bet."""
    end = now.astimezone(MEL).date() - timedelta(days=now.astimezone(MEL).weekday())   # this Monday
    start = end - timedelta(days=7)
    days = {(start + timedelta(days=i)).isoformat() for i in range(7)}
    fmt = lambda u: f"{'+' if u >= 0 else '−'}{abs(u):.2f}U"
    mem = record.get("members") or {}
    lines, any_ = [], False
    for name, units, clv in (("💎 VIP system", record.get("units"), record.get("clv_days")),
                             ("⭐ Founding Members & Daily Card", mem.get("units"), mem.get("clv_days"))):
        legs, cl = [], []
        for d in days:
            l, c = _day(units, clv, d)
            legs += l
            cl += c
        if not legs:
            continue
        any_ = True
        u, w = sum(l[1] for l in legs), sum(l[2] == "win" for l in legs)
        best, worst = max(legs, key=lambda l: l[1]), min(legs, key=lambda l: l[1])
        lines += ["", f"**{name}: {fmt(u)}** · {w}-{len(legs) - w}" + (f" · {sum(cl) / len(cl):+.1f}% avg CLV" if cl else ""),
                  f"🔥 Best: {best[0]} {fmt(best[1])}", f"🧊 Worst: {worst[0]} {fmt(worst[1])}"]
    if not any_:
        print(f"discord weekly: no graded bets {start} to {end - timedelta(days=1)}")
        return False
    last = end - timedelta(days=1)
    ok = send("results", f"🗓 Week in review · {start.day} {start.strftime('%b')} – {last.day} {last.strftime('%b')}", lines,
              "https://overlay.tips/record.html")
    print(f"discord weekly: {'posted' if ok else 'NOT posted'}")
    return ok


def teaser(b):
    """A VIP-only bet just won: show #chat what VIP had (after the result, so nothing is given away)."""
    stake = b.get("stake") or 1
    won = (b["price"] - 1) * stake
    game = f" · {b['game']}" if b.get("game") else ""
    return send("chat", f"💎 VIP cashed: {b.get('sel', '')} @ ${b['price']:.2f} ✅",
                [f"+{won:.2f}U on {stake:g}U{game}",
                 "VIP members got this the moment the price was there, plus every game bet, racing value bet and arb."],
                "https://overlay.tips/membership.html")


if __name__ == "__main__":   # post yesterday's results now:  python scripts/discord_post.py record.json
    import sys
    from datetime import timezone
    with open(sys.argv[1]) as f:
        rec = json.load(f)
    print("channels:", sorted(_channel and (lambda: (_channel("results"), _chans)[1])() or []))
    daily_results(rec, datetime.now(timezone.utc))
