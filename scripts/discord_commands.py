"""Registers the bot's slash commands (/stats, /why) in the Overlay Tips server, and prints what the
/stats Cloudflare Worker (cloudflare/discord-worker.js) needs: the app's public key and the member role ids.

Safe to re-run. Env: DISCORD_BOT_TOKEN, DISCORD_GUILD_ID.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import discord_post

COMMANDS = [{
    "name": "stats", "type": 1,
    "description": "Live NBA stats for a player tonight",
    "options": [{"type": 3, "name": "player", "description": "Player name, e.g. Zion or Jalen Brunson", "required": True}],
}, {
    "name": "why", "type": 1,
    "description": "Why is this player on today's board? Gemini explains the pick from our numbers",
    "options": [{"type": 3, "name": "player", "description": "Player name, e.g. Jalen Brunson", "required": True}],
}]


def main():
    if not discord_post.enabled():
        sys.exit("DISCORD_BOT_TOKEN / DISCORD_GUILD_ID not set")
    app = discord_post._call("GET", "/oauth2/applications/@me")
    discord_post._call("PUT", f"/applications/{app['id']}/guilds/{discord_post.GUILD}/commands", COMMANDS)
    print("registered:", ", ".join("/" + c["name"] for c in COMMANDS))
    print("DISCORD_PUBLIC_KEY for the Worker:", app.get("verify_key"))
    roles = {r["name"]: r["id"] for r in discord_post._call("GET", f"/guilds/{discord_post.GUILD}/roles")}
    ids = [roles[n] for n in ("Founder", "Daily Card", "VIP") if n in roles]
    print("ALLOWED_ROLE_IDS for the Worker:", ",".join(ids))


if __name__ == "__main__":
    main()
