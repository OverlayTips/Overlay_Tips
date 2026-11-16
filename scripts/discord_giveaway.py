"""Discord giveaways in #giveaways: members react 🎉 to enter, the bot draws the winner(s) when it ends.

  python scripts/discord_giveaway.py start "Prize" HOURS [WINNERS] [who]   who: everyone | founder | core | vip
  python scripts/discord_giveaway.py draw-due     (every odds update: draws any giveaway whose time is up)

Start it from GitHub: Actions -> "Discord giveaway" -> Run workflow (prize, hours, winners, who can enter).
Everything lives in the giveaway message itself (end time, winners, who can enter), so there's no extra state.
Winners are picked with a cryptographically fair random draw among eligible people who reacted 🎉 (bots and
anyone without the required role are skipped), announced in #giveaways with a mention, and the post is marked ended.

Env: DISCORD_BOT_TOKEN, DISCORD_GUILD_ID.
"""
import os, re, secrets, sys, time, urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import discord_post as dp

EMOJI = "🎉"
ROLE_OF = {"founder": ["Founder", "Daily Card", "VIP"], "core": ["Daily Card", "VIP"], "vip": ["VIP"]}
WHO_TXT = {"everyone": "Everyone in the server", "founder": "Founding members, Daily Card and VIP",
           "core": "Daily Card and VIP members", "vip": "VIP members only"}
OPEN = "React 🎉 below to enter."


def parse(content):
    """(end, winners, who) from an open giveaway post, or None."""
    if OPEN not in content:
        return None
    end, n = re.search(r"Ends <t:(\d+):R>", content), re.search(r"· (\d+) winner", content)
    who = next((k for k, v in WHO_TXT.items() if f"Who can enter: {v}" in content), "everyone")
    return (int(end.group(1)), int(n.group(1)) if n else 1, who) if end else None


def channel():
    cid = dp._channel("giveaways")
    if not cid:
        sys.exit("No #giveaways channel yet: run Actions -> Discord setup first.")
    return cid


def start(prize, hours, winners=1, who="everyone"):
    end = int(time.time() + float(hours) * 3600)
    who = who if who in WHO_TXT else "everyone"
    text = (f"# {EMOJI} GIVEAWAY {EMOJI}\n**{prize}**\n\n"
            f"{OPEN}\n"
            f"Ends <t:{end}:R> (<t:{end}:f>) · {winners} winner{'s' if int(winners) > 1 else ''}\n"
            f"Who can enter: {WHO_TXT[who]}\n"
            f"-# Free to enter. 18+ Australian residents. Winner drawn at random by the bot and announced here. "
            f"Full T&Cs: <https://overlay.tips/terms.html>")
    cid = channel()
    m = dp._call("POST", f"/channels/{cid}/messages", {"content": text, "allowed_mentions": {"parse": []}})
    dp._call("PUT", f"/channels/{cid}/messages/{m['id']}/reactions/{urllib.parse.quote(EMOJI)}/@me")
    print(f"giveaway started: {prize}, ends {datetime.fromtimestamp(end, timezone.utc).isoformat()}")


def entrants(cid, mid):
    users, after = [], None
    while True:
        q = f"limit=100" + (f"&after={after}" if after else "")
        page = dp._call("GET", f"/channels/{cid}/messages/{mid}/reactions/{urllib.parse.quote(EMOJI)}?{q}") or []
        users += [u for u in page if not u.get("bot")]
        if len(page) < 100:
            return users
        after = page[-1]["id"]


def draw(cid, m, n, who):
    users = entrants(cid, m["id"])
    if who in ROLE_OF:   # keep only people holding one of the allowed roles
        roles = {r["name"]: r["id"] for r in dp._call("GET", f"/guilds/{dp.GUILD}/roles")}
        allowed = {roles[r] for r in ROLE_OF[who] if r in roles}
        ok = []
        for u in users:
            try:
                mem = dp._call("GET", f"/guilds/{dp.GUILD}/members/{u['id']}")
                if allowed & set(mem.get("roles") or []):
                    ok.append(u)
            except Exception:
                pass   # left the server
        users = ok
    rng, pool, won = secrets.SystemRandom(), list(users), []
    while pool and len(won) < n:
        won.append(pool.pop(rng.randrange(len(pool))))
    prize = (m["content"].split("\n")[1] if "\n" in m["content"] else "").strip("*")
    if won:
        names = ", ".join(f"<@{u['id']}>" for u in won)
        msg = (f"{EMOJI} **Giveaway winner{'s' if len(won) > 1 else ''}: {names}!**\nYou won **{prize}**. "
               f"DM the Overlay Tips team to claim it. ({len(users)} entered)")
        dp._call("POST", f"/channels/{cid}/messages", {"content": msg, "allowed_mentions": {"users": [u["id"] for u in won]},
                                                       "message_reference": {"message_id": m["id"]}})
    else:
        dp._call("POST", f"/channels/{cid}/messages", {"content": f"Giveaway for **{prize}** ended with no eligible entries.",
                                                       "message_reference": {"message_id": m["id"]}})
    ended = m["content"].replace(OPEN, f"Ended · {len(users)} entered").replace(f"# {EMOJI} GIVEAWAY {EMOJI}", "# GIVEAWAY (ENDED)")
    dp._call("PATCH", f"/channels/{cid}/messages/{m['id']}", {"content": ended})
    print(f"giveaway drawn: {prize}: {len(won)} winner(s) from {len(users)} entries")


def draw_due():
    if not dp.enabled():
        return
    cid = dp._channel("giveaways")
    if not cid:
        return
    now = time.time()
    for m in dp._call("GET", f"/channels/{cid}/messages?limit=50") or []:
        if not (m.get("author") or {}).get("bot"):
            continue
        t = parse(m.get("content") or "")
        if t and t[0] <= now:
            try:
                draw(cid, m, t[1], t[2])
            except Exception as e:
                print(f"::warning::giveaway draw failed: {e}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "start":
        start(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 1, sys.argv[5] if len(sys.argv) > 5 else "everyone")
    elif cmd == "draw-due":
        draw_due()
    else:
        sys.exit(__doc__)
