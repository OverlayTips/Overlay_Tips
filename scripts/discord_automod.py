"""Sets up Discord AutoMod in the Overlay Tips server so offensive messages are blocked before anyone sees them.

Rules (all by Discord's own filters, kept up to date by Discord):
  - Slurs + sexual content: message blocked (Discord's built-in word lists)
  - Extra blocked words: anything in BLOCKED below -> blocked + 10 min timeout
  - Spam: Discord's spam detector blocks the message
  - Mention spam: 5+ @mentions in one message -> blocked + 10 min timeout
Safe to re-run: rules are matched by name and updated. Env: DISCORD_BOT_TOKEN, DISCORD_GUILD_ID.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import discord_post as d

# extra words/phrases to block (Discord wildcards: *word* matches inside other words). Add your own here.
BLOCKED = ["*ray/cist*", "*ray cist*", "*kys*", "*kill yourself*", "*neck yourself*"]
BLOCK_MSG = {"type": 1, "metadata": {"custom_message": "Blocked by Overlay Tips: keep it respectful. Repeat breaches = removal."}}
TIMEOUT = {"type": 3, "metadata": {"duration_seconds": 600}}

RULES = [
    {"name": "Overlay: slurs + sexual content", "event_type": 1, "trigger_type": 4,
     "trigger_metadata": {"presets": [2, 3]}, "actions": [BLOCK_MSG]},
    {"name": "Overlay: blocked words", "event_type": 1, "trigger_type": 1,
     "trigger_metadata": {"keyword_filter": BLOCKED}, "actions": [BLOCK_MSG, TIMEOUT]},
    {"name": "Overlay: spam", "event_type": 1, "trigger_type": 3, "actions": [BLOCK_MSG]},
    {"name": "Overlay: mention spam", "event_type": 1, "trigger_type": 5,
     "trigger_metadata": {"mention_total_limit": 5, "mention_raid_protection_enabled": True}, "actions": [BLOCK_MSG, TIMEOUT]},
]


def main():
    if not d.enabled():
        sys.exit("DISCORD_BOT_TOKEN / DISCORD_GUILD_ID not set")
    have = {r["name"]: r for r in d._call("GET", f"/guilds/{d.GUILD}/auto-moderation/rules") or []}
    for r in RULES:
        body = {**r, "enabled": True}
        try:
            if r["name"] in have:
                body = {k: v for k, v in body.items() if k not in ("trigger_type",)}   # can't change a rule's trigger type
                d._call("PATCH", f"/guilds/{d.GUILD}/auto-moderation/rules/{have[r['name']]['id']}", body)
                print("updated:", r["name"])
            else:
                d._call("POST", f"/guilds/{d.GUILD}/auto-moderation/rules", body)
                print("created:", r["name"])
        except Exception as e:
            detail = e.read()[:300] if hasattr(e, "read") else ""
            print(f"::warning::automod {r['name']}: {e} {detail!r}")


if __name__ == "__main__":
    main()
