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
        ("membership", True, ["pricing", "prices"]),
        ("giveaways", True, ["giveaway"]),
        ("announcements", True, []),
        ("results", True, ["resources"]),
    ]),
    ("FREE", None, [
        ("chat", False, ["general"]),
        ("bet-of-the-day", True, ["best-bet-of-day", "meeting-plans"]),
        ("racing-top-picks", True, []),
    ]),
    ("OVERLAY WIRE", ["Founder", "Daily Card", "VIP"], [   # same as the site: free sign-up and up
        ("nba-lineups", True, ["lineups"]),
        ("previews", True, ["preview"]),
    ]),
    ("FOUNDERS", ["Founder", "Daily Card", "VIP"], [
        ("founder-picks", True, []),
    ]),
    ("DAILY CARD", ["Daily Card", "VIP"], [
        ("nba-props", True, []),
        ("nbl-props", True, []),
        ("nfl-props", True, []),
        ("other-props", True, []),
        ("live-tracker", True, []),
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

**Founder (free):** bet of the day, racing top picks + #founder-picks: the props with a good matchup and a verdict we like
**Overlay Wire (free sign-up):** #nba-lineups (confirmed starters + injury news) and #previews (the daily preview)
**Daily Card:** every player prop alert (NBA, NBL, NFL)
**VIP:** everything, plus game bets, racing value, arbs and steamers

Join or upgrade at <https://overlay.tips/membership.html>

Price signals, not sure things. 18+ only. Only bet what you can afford to lose.
Gambling Help Online 1800 858 858 · <https://www.betstop.gov.au>"""


BOT_NICK = "Overlay Tips"

RULES = """**📜 Server rules**
1. **18+ only.** Under 18 = removed.
2. **Respect everyone.** No racism, sexism, homophobia, slurs or hate of any kind, including jokes.
3. **No offensive profiles.** Names, nicknames, pronouns, bios and avatars count. Removed without warning.
4. **No spam or self-promo.** No other tipping services, referral links, ads or DMing members to sell stuff.
5. **Don't share paid tips.** What's posted in the Daily Card and VIP channels stays in the server.
6. **No harassment.** No abuse over losing bets, no pile-ons, no unwanted DMs.
7. **Bet responsibly.** Don't pressure anyone to bet, chase losses or bet more than they can afford.
8. **Mods have the final say.** Breaking the rules = warning, timeout, kick or ban, depending on how bad.

AutoMod blocks offensive messages automatically. See something? Tag a mod or DM @overlay_tips.
-# Gambling Help Online 1800 858 858 · <https://www.betstop.gov.au>"""


GIVEAWAY = """**🎁 Refer a mate. You both get $50.**
Every mate you refer = 1 entry. If you're drawn, you and your mate get **$50 cash each** via PayID.
1. Follow @overlay_tips on Instagram
2. Enter at <https://overlay.tips/giveaway.html>
3. Share your link: overlay.tips/?ref=YOURHANDLE · more mates = more entries

Entries close **Sun 22 Nov, 11:59pm AEDT**. Drawn live on @overlay_tips Instagram, Mon 23 Nov.
Discord-only giveaways get posted in this channel too: react 🎉 to enter.
-# Free to enter. 18+ Australian residents only. No betting account needed. T&Cs: <https://overlay.tips/terms.html>"""

BILLING = "https://overlay-billing.borgjayden005.workers.dev/plans"   # live Stripe prices (cloudflare/billing-worker.js)


def membership_post():
    """The #membership message: plans, what's in each, and live prices from Stripe."""
    try:
        req = urllib.request.Request(BILLING, headers={"User-Agent": "overlay-tips/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            plans = json.load(r).get("plans") or {}
    except Exception as e:
        print("membership: couldn't read prices:", e)
        plans = {}

    def price(key):
        p = plans.get(key)
        if not p or p.get("amount") is None:
            return None
        amt = float(p["amount"])   # already in dollars (same as membership.html)
        return (f"${amt:,.2f}" if amt % 1 else f"${amt:,.0f}") + f"/{p.get('interval', '')}"

    def line(m, w):
        a, b = price(m), price(w)
        return " or ".join(x for x in (a, b) if x) or "see the site for prices"
    return f"""**💳 Overlay Tips memberships**

**⭐ Founding member · FREE for life** (first 500 only)
• Bet of the day + racing top picks
• #founder-picks: the props where the matchup is good and the verdict is one we like
• Every tip in the Discord
• Overlay Wire, Hot stats and the bet tracker
• #nba-lineups (confirmed starters + injury news) and #previews

**🃏 Daily Card · {line("core-month", "core-week")}**
Everything in Founding, plus:
• Every player prop alert (NBA, NBL, NFL) in the Daily Card channels
• Live bet + SGM tracking for NBA and NBL, and the #live-tracker channel
• Unit sizes and the reasoning for each play

**💎 VIP · {line("vip-month", "vip-week")}**
Everything in the Daily Card, plus:
• The odds tracker: every Aussie bookie side by side
• Game bets, racing value, arbs and steamers channels
• Tips the moment they're locked, before the price moves
• Saturday racing best bets + weekly bonus bet conversions

**How to join:** sign up at <https://overlay.tips/portal.html>, pick a plan, then tap **Connect Discord** on the Group & alerts tab. Your role (and channels) unlock automatically within about 5 minutes.

Refer a mate: they get 50% off their first month, you get a week free. Cancel anytime, no lock-in.
-# 18+ only. Analysed tips, not guaranteed wins. Gamble responsibly · 1800 858 858"""


def post_or_update(channel_id, content):
    """Keep one bot message in the channel: edit it if it's there, otherwise post and pin it."""
    msgs = call("GET", f"/channels/{channel_id}/messages?limit=50") or []
    head = content.split("\n", 1)[0]   # the bot's message with the same first line
    mine = [m for m in msgs if (m.get("author") or {}).get("bot") and (m.get("content") or "").split("\n", 1)[0] == head]
    for extra in mine[:-1]:   # duplicates of this post (newest first from the API): keep the oldest, delete the rest
        call("DELETE", f"/channels/{channel_id}/messages/{extra['id']}")
    for m in msgs:   # the "pinned a message" notices the bot's own pins leave behind
        if m.get("type") == 6 and (m.get("author") or {}).get("bot"):
            try:
                call("DELETE", f"/channels/{channel_id}/messages/{m['id']}")
            except RuntimeError:
                pass
    mine = mine[-1:]
    if mine:
        if mine[-1].get("content") != content:
            call("PATCH", f"/channels/{channel_id}/messages/{mine[-1]['id']}", {"content": content})
            return "updated"
        return "unchanged"
    m = call("POST", f"/channels/{channel_id}/messages", {"content": content})
    try:
        call("PUT", f"/channels/{channel_id}/pins/{m['id']}")
    except RuntimeError:
        pass
    return "posted"


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
    try:   # the bot's application (client) id: public, used by the portal's "Connect Discord" button
        print("application id:", call("GET", "/oauth2/applications/@me")["id"])
    except RuntimeError as e:
        print("application id: couldn't read", e)
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

    try:   # the bot's account name and its name in the server both = "Overlay Tips"
        if (call("GET", "/users/@me") or {}).get("username") != BOT_NICK:
            call("PATCH", "/users/@me", {"username": BOT_NICK})
        call("PATCH", f"/guilds/{GUILD}/members/@me", {"nick": BOT_NICK})
        print("bot name:", BOT_NICK)
    except Exception as e:
        print(f"::warning::couldn't set the bot's name: {e} {e.read()[:200] if hasattr(e, 'read') else ''}")
    start = find(TEXT, ["start-here"])
    if start:
        print("welcome:", post_or_update(start["id"], WELCOME))
        print("rules:", post_or_update(start["id"], RULES))
    gw = find(TEXT, ["giveaways"])
    if gw:
        print("giveaway post:", post_or_update(gw["id"], GIVEAWAY))
    mem = find(TEXT, ["membership"])
    if mem:
        print("membership:", post_or_update(mem["id"], membership_post()))
    print("done")


if __name__ == "__main__":
    main()
