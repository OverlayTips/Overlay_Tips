"""One-off (safe to re-run) setup of the Overlay Tips Discord server through the bot.

Builds the layout: roles (VIP, Daily Card, Founder), categories and channels, private paid categories,
read-only alert channels, and the welcome post. Renames the default "community" template channels
instead of duplicating them. Every step checks what's already there, so running it twice changes nothing.

Env: DISCORD_BOT_TOKEN (bot added to the server with Administrator), DISCORD_GUILD_ID (server id).
Run from GitHub: Actions -> "Discord setup" -> Run workflow.
"""
import json, os, sys, time, urllib.error, urllib.request

API = "https://discord.com/api/v10"
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
GUILD = os.environ.get("DISCORD_GUILD_ID", "").strip()
VIEW, SEND = 1 << 10, 1 << 11
TEXT, VOICE, CATEGORY = 0, 2, 4

ROLES = [("VIP", 0xEFBE3B), ("Daily Card", 0x62F0A8), ("Founder", 0x87978F)]

# (category, who can see it (None = everyone), [(channel, read_only, old names to rename from)])
LAYOUT = [
    ("Information", None, [
        ("start-here", True, ["welcome-and-rules", "welcome", "rules"]),
        ("announcements", True, []),
        ("results", True, ["resources"]),
    ]),
    ("FREE", None, [
        ("chat", False, ["general"]),
        ("bet-of-the-day", True, ["best-bet-of-day", "meeting-plans"]),
        ("racing-top-picks", True, []),
    ]),
    ("DAILY CARD", ["Daily Card", "VIP"], [
        ("nba-props", True, []),
        ("nbl-props", True, []),
        ("nfl-props", True, []),
    ]),
    ("VIP", ["VIP"], [
        ("game-bets", True, []),
        ("racing-value", True, []),
        ("arbs-steamers", True, []),
    ]),
]
CATEGORY_OLD = {"FREE": ["Text Channels"]}
DELETE = {"off-topic", "meeting-room", "Meeting Room", "free"}   # "free" channel: replaced by the FREE category

WELCOME = """**Welcome to Overlay Tips 🏇🏀**
We find bookie prices that beat the fair odds and post every bet, wins and losses, tracked live on <https://overlay.tips/record.html>.

**Founder (free):** bet of the day + racing top picks
**Daily Card:** every player prop alert (NBA, NBL, NFL)
**VIP:** everything, plus game bets, racing value, arbs and steamers

Join or upgrade at <https://overlay.tips/membership.html>

Price signals, not sure things. 18+ only. Only bet what you can afford to lose.
Gambling Help Online 1800 858 858 · <https://www.betstop.gov.au>"""


def call(method, path, body=None):
    for _ in range(6):
        req = urllib.request.Request(API + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json",
                                              "User-Agent": "DiscordBot (https://overlay.tips, 1.0)"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(json.loads(e.read() or b"{}").get("retry_after", 1)) + 0.2)
                continue
            raise RuntimeError(f"{method} {path}: HTTP {e.code} {e.read()[:300].decode(errors='ignore')}")
    raise RuntimeError(f"{method} {path}: rate limited")


def main():
    if not (TOKEN and GUILD):
        sys.exit("Add the DISCORD_BOT_TOKEN and DISCORD_GUILD_ID secrets first.")
    roles = {r["name"]: r["id"] for r in call("GET", f"/guilds/{GUILD}/roles")}
    for name, color in ROLES:
        if name not in roles:
            roles[name] = call("POST", f"/guilds/{GUILD}/roles", {"name": name, "color": color, "hoist": True})["id"]
            print("role created:", name)
    everyone = GUILD   # the @everyone role has the server's id

    chans = call("GET", f"/guilds/{GUILD}/channels")
    gone = [c for c in chans if c.get("type") in (TEXT, VOICE) and c.get("name") in DELETE]
    for c in gone:
        call("DELETE", f"/channels/{c['id']}")
        print("deleted:", c["name"])
    chans = [c for c in chans if c not in gone]

    def find(kind, names):
        for n in names:
            for c in chans:
                if c.get("type") == kind and str(c.get("name", "")).lower() == n.lower():
                    return c
        return None

    for pos, (cat, who, items) in enumerate(LAYOUT):
        cat_ow = [] if who is None else ([{"id": everyone, "type": 0, "allow": "0", "deny": str(VIEW)}] +
                                         [{"id": roles[r], "type": 0, "allow": str(VIEW), "deny": "0"} for r in who])
        c = find(CATEGORY, [cat] + CATEGORY_OLD.get(cat, []))
        if c:
            call("PATCH", f"/channels/{c['id']}", {"name": cat, "position": pos, "permission_overwrites": cat_ow})
        else:
            c = call("POST", f"/guilds/{GUILD}/channels", {"name": cat, "type": CATEGORY, "position": pos,
                                                           "permission_overwrites": cat_ow})
            chans.append(c)
        print("category:", cat)
        for i, (name, ro, old) in enumerate(items):
            ow = [dict(o) for o in cat_ow]
            if ro:   # members read, only the bot (admin) posts
                e = next((o for o in ow if o["id"] == everyone), None)
                if e:
                    e["deny"] = str(int(e["deny"]) | SEND)
                else:
                    ow.append({"id": everyone, "type": 0, "allow": "0", "deny": str(SEND)})
            ch = find(TEXT, [name] + old)
            body = {"name": name, "parent_id": c["id"], "position": i, "permission_overwrites": ow}
            if ch:
                call("PATCH", f"/channels/{ch['id']}", body)
            else:
                ch = call("POST", f"/guilds/{GUILD}/channels", {**body, "type": TEXT})
                chans.append(ch)
            print("  #" + name + (" (read-only)" if ro else ""))

    start = find(TEXT, ["start-here"])
    if start:
        msgs = call("GET", f"/channels/{start['id']}/messages?limit=20") or []
        if not any((m.get("author") or {}).get("bot") for m in msgs):
            m = call("POST", f"/channels/{start['id']}/messages", {"content": WELCOME})
            try:
                call("PUT", f"/channels/{start['id']}/pins/{m['id']}")
            except RuntimeError:
                pass
            print("welcome posted")
    print("done")


if __name__ == "__main__":
    main()
