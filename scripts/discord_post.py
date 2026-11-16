"""Posts tracker alerts and the daily results into the Overlay Tips Discord, through the bot.

Channels are found by name (the layout scripts/discord_setup.py builds), so nothing breaks if ids change.
Routing mirrors the app alerts (value_alerts.app_push): props -> Daily Card channels, game bets / racing value /
arbs -> VIP channels, bet of the day + racing top picks -> FREE channels. Never raises: Discord being down or
the secrets missing just skips the post.

Env: DISCORD_BOT_TOKEN, DISCORD_GUILD_ID.
"""
import json, os, urllib.error, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

API = "https://discord.com/api/v10"
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
GUILD = os.environ.get("DISCORD_GUILD_ID", "").strip()
MEL = ZoneInfo("Australia/Melbourne")
FOOT = "-# Price signals, not sure things. 18+ · Gamble responsibly · 1800 858 858"
_chans = None


def enabled():
    return bool(TOKEN and GUILD)


def _call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json",
                                          "User-Agent": "DiscordBot (https://overlay.tips, 1.0)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


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


def send(channel, title, lines, url=None):
    """Post one message. Returns True if it went."""
    if not enabled():
        return False
    try:
        cid = _channel(channel)
        if not cid:
            print(f"::warning::discord: no #{channel} channel (run the Discord setup workflow)")
            return False
        body = "\n".join(l for l in lines if l is not None)
        text = f"**{title}**\n{body}" + (f"\n<{url}>" if url else "") + f"\n{FOOT}"
        _call("POST", f"/channels/{cid}/messages", {"content": text[:1990], "allowed_mentions": {"parse": []}})
        return True
    except Exception as e:
        print(f"::warning::discord post to #{channel} failed: {type(e).__name__} {e}")
        return False


def alert(cat, title, lines, url, min_tier):
    return send(channel_for(cat, min_tier), title, lines, url)


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


if __name__ == "__main__":   # post yesterday's results now:  python scripts/discord_post.py record.json
    import sys
    from datetime import timezone
    with open(sys.argv[1]) as f:
        rec = json.load(f)
    print("channels:", sorted(_channel and (lambda: (_channel("results"), _chans)[1])() or []))
    daily_results(rec, datetime.now(timezone.utc))
