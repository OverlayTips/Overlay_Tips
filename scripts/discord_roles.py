"""Keeps members' Discord roles in step with their Overlay Tips plan. Runs every odds update.

1. New links (a member pressed "Connect Discord" in the portal; supabase/discord.sql): check the Discord token,
   add them to the server (guilds.join), record their Discord id and name, then throw the token away.
2. Anyone whose plan changed since the last sync: give the role for their tier (VIP / Daily Card / Founder) and
   take the other two away. Admins count as VIP; "pending" (not active) gets none of the three.
Only those three roles are ever touched. Best effort: problems are noted on the member's row and retried next run.

Env: SUPABASE_SERVICE_KEY, SUPABASE_URL (optional), DISCORD_BOT_TOKEN, DISCORD_GUILD_ID.
"""
import json, os, urllib.error, urllib.parse, urllib.request

SB = os.environ.get("SUPABASE_URL", "https://enldqvdxygppxwrauqlw.supabase.co").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
GUILD = os.environ.get("DISCORD_GUILD_ID", "").strip()
API = "https://discord.com/api/v10"
ROLE_FOR = {"vip": "VIP", "core": "Daily Card", "founder": "Founder"}


class HTTPErr(Exception):
    def __init__(self, code, text):
        super().__init__(f"HTTP {code} {text}")
        self.code = code


def _req(method, url, headers, body=None):
    req = urllib.request.Request(url, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise HTTPErr(e.code, e.read()[:200].decode(errors="ignore"))


def sb(method, path, body=None):
    return _req(method, f"{SB}/rest/v1/{path}", {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
                                                 "Content-Type": "application/json", "Prefer": "return=minimal"}, body)


def bot(method, path, body=None):
    return _req(method, API + path, {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json",
                                     "User-Agent": "DiscordBot (https://overlay.tips, 1.0)"}, body)


def save(uid, **cols):
    sb("PATCH", f"discord_links?user_id=eq.{uid}", cols)


def main():
    if not (SB_KEY and TOKEN and GUILD):
        print("discord roles: secrets not set, skipping")
        return
    try:
        links = sb("GET", "discord_links?select=user_id,access_token,discord_id,synced_tier,profiles(tier,is_admin)") or []
    except HTTPErr as e:
        print(f"discord roles: can't read links ({e}). Run supabase/discord.sql once.")
        return
    roles = {r["name"]: r["id"] for r in bot("GET", f"/guilds/{GUILD}/roles")}
    managed = {n: roles[n] for n in ROLE_FOR.values() if n in roles}
    joined = synced = 0
    for L in links:
        p = L.get("profiles") or {}
        tier = "vip" if p.get("is_admin") else (p.get("tier") or "pending")
        want = managed.get(ROLE_FOR.get(tier, ""))
        uid, did = L["user_id"], L.get("discord_id")
        try:
            if L.get("access_token"):   # new or re-connected: verify with Discord and add them to the server
                tok = L["access_token"]
                try:
                    me = _req("GET", API + "/users/@me", {"Authorization": f"Bearer {tok}",
                                                         "User-Agent": "DiscordBot (https://overlay.tips, 1.0)"})
                except HTTPErr as e:
                    save(uid, access_token=None, error="Discord connection expired, connect again" if e.code == 401 else str(e)[:200])
                    continue
                did = me["id"]
                try:
                    bot("PUT", f"/guilds/{GUILD}/members/{did}", {"access_token": tok, "roles": [want] if want else []})
                except HTTPErr as e:
                    print(f"discord roles: couldn't add {did} to the server: {e}")
                save(uid, discord_id=did, discord_name=me.get("global_name") or me.get("username"),
                     access_token=None, synced_tier=None, error=None)
                L["synced_tier"] = None
                joined += 1
            if not did or L.get("synced_tier") == tier:
                continue
            try:
                member = bot("GET", f"/guilds/{GUILD}/members/{did}")
            except HTTPErr as e:
                if e.code == 404:
                    save(uid, error="Not in the Discord server: connect again to join")
                    continue
                raise
            have = set(member.get("roles") or [])
            for name, rid in managed.items():
                if rid == want and rid not in have:
                    bot("PUT", f"/guilds/{GUILD}/members/{did}/roles/{rid}")
                elif rid != want and rid in have:
                    bot("DELETE", f"/guilds/{GUILD}/members/{did}/roles/{rid}")
            save(uid, synced_tier=tier, error=None)
            synced += 1
        except Exception as e:
            print(f"discord roles: {uid[:8]}: {type(e).__name__} {e}")
    print(f"discord roles: {len(links)} linked, {joined} newly joined, {synced} roles updated")


if __name__ == "__main__":
    main()
