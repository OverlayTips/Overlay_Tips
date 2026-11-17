"""Welcome DM for people who join the Overlay Discord. Runs every odds update.

Checks the server's member list, DMs anyone new with how to unlock their channels (Connect Discord on the site),
and falls back to a welcome mention in #chat if their DMs are closed. People already in the server the first
time this runs are just remembered, not messaged.

Needs "Server Members Intent" switched on for the bot (Developer Portal -> Bot -> Privileged Gateway Intents).
State: odds-data/discord_state.json (encrypted with ODDS_ALERTS_KEY: who has been welcomed).
Env: DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, ODDS_ALERTS_KEY, OUT (default odds-data).
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import discord_post as dp
import picks

OUT = os.environ.get("OUT", "odds-data")
STATE = os.path.join(OUT, "discord_state.json")
KEY = os.environ.get("ODDS_ALERTS_KEY", "").strip()

WELCOME = """**Welcome to Overlay Tips 👋**
To unlock your channels, link your Discord to your Overlay account (free):
1. Sign up or log in at <https://overlay.tips/portal.html>
2. Open the **Group & alerts** tab and tap **Connect Discord**
Your role and channels unlock within about 5 minutes.

⭐ **Founder (free):** bet of the day, racing top picks, #founder-picks + #founder-tracker (live)
🃏 **Daily Card:** every player prop alert
💎 **VIP:** everything, plus game bets, racing value and arbs
Plans and prices: #membership · Results: #results
-# 18+ only. Gamble responsibly · 1800 858 858"""


def load():
    try:
        with open(STATE) as f:
            return picks.decrypt_with_key(json.load(f), KEY)
    except Exception:
        return {}


def save(st):
    with open(STATE, "w") as f:
        json.dump(picks.encrypt_with_key(st, KEY), f)


def members():
    out, after = [], "0"
    while True:
        page = dp._call("GET", f"/guilds/{dp.GUILD}/members?limit=1000&after={after}") or []
        out += page
        if len(page) < 1000:
            return out
        after = page[-1]["user"]["id"]


def main():
    if not (dp.enabled() and KEY):
        print("discord welcome: secrets not set, skipping")
        return
    try:
        ms = [m for m in members() if not (m.get("user") or {}).get("bot")]
    except Exception as e:
        print(f"::warning::discord welcome: can't list members ({e}). "
              "Turn on Server Members Intent: Developer Portal -> Bot -> Privileged Gateway Intents.")
        return
    text = WELCOME
    for name in ("founder-picks", "founder-tracker", "membership", "results"):   # make the channel names clickable
        cid = dp._channel(name)
        if cid:
            text = text.replace("#" + name, f"<#{cid}>")
    st = load()
    seen = set(st.get("welcomed") or [])
    first = "welcomed" not in st
    sent = 0
    for m in ms:
        uid = m["user"]["id"]
        if uid in seen:
            continue
        seen.add(uid)
        if first:
            continue   # already here before the welcome existed
        ok = False
        try:
            ch = dp._call("POST", "/users/@me/channels", {"recipient_id": uid})
            dp._call("POST", f"/channels/{ch['id']}/messages", {"content": text})
            ok = True
        except Exception as e:
            print(f"discord welcome: DM to {uid} failed ({e}), welcoming in #chat")
        if not ok:
            try:
                cid = dp._channel("chat")
                if cid:
                    dp._call("POST", f"/channels/{cid}/messages", {
                        "content": f"Welcome <@{uid}> 👋 Link your Discord at <https://overlay.tips/portal.html> "
                                   "(Group & alerts → Connect Discord) to unlock your channels. Plans: #membership",
                        "allowed_mentions": {"users": [uid]}})
            except Exception as e:
                print(f"discord welcome: #chat fallback failed ({e})")
        sent += 1
    st["welcomed"] = sorted(seen)
    save(st)
    print(f"discord welcome: {len(ms)} members, {sent} welcomed" + (" (first run: existing members remembered)" if first else ""))


if __name__ == "__main__":
    main()
