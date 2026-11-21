"""Live prices on the Discord alerts: every alert the tracker posts gets a "Live prices" panel (an embed on the same
message, so the write-up isn't touched) that's edited after every odds update until the game starts. It shows every
bookie's current price on that exact selection (player, market, side, line), how each moved since the alert, and
whether it's still value against the fair price we alerted on. At the start it locks: "Closed at the start".

value_alerts.py records each posted alert message (live_msgs.json, encrypted with ODDS_ALERTS_KEY, since the branch is
public and these are members' bets); this script reads the fresh odds (data.json, same as bet_log.py) and edits them.
Run right after value_alerts.py in the odds workflow (every 5 min daytime, 15 overnight; props re-pull every 30 min).

Env: ODDS_ALERTS_KEY, ODDS_PAGE_PASSWORD (read data.json), DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, OUT (default odds-data).
"""
import hashlib, json, os, sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import discord_post

OUT = os.environ.get("OUT", "odds-data")
PATH = os.path.join(OUT, "live_msgs.json")
KEY = os.environ.get("ODDS_ALERTS_KEY", "").strip()
MEL = ZoneInfo("Australia/Melbourne")
MAX_BOOKS = 10
GREEN, RED, GREY = 0x2ECC71, 0xE74C3C, 0x95A5A6


def _parse(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))


def load():
    if not KEY:
        return []
    try:
        import picks
        with open(PATH) as f:
            return picks.decrypt_with_key(json.load(f), KEY) or []
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"::warning::live prices: couldn't read {PATH}: {e}")
        return []


def save(items):
    import picks
    with open(PATH, "w") as f:
        json.dump(picks.encrypt_with_key(items, KEY), f)


def add(p, msgs):
    """Called by value_alerts.py after an alert is posted: remember its Discord messages for live price edits."""
    lg = p.get("log") or {}
    if not (KEY and msgs and lg.get("ev") and lg.get("mk") and lg.get("side") and p.get("start")):
        return
    items = load()
    rid = f"{p['key']}|{p['start']}"
    ids = [[m["channel_id"], m["id"]] for m in msgs if isinstance(m, dict) and m.get("id")]
    old = next((x for x in items if x["id"] == rid), None)
    if old:   # re-alert (better price): track the new messages too, start from the new price
        old["msgs"] += [i for i in ids if i not in old["msgs"]]
        old.update(price=p["price"], book=p["book"])
    else:
        items.append({"id": rid, "msgs": ids, "sel": p.get("sel", ""), "ev": lg["ev"], "mk": lg["mk"],
                      "desc": lg.get("desc") or "", "side": lg["side"], "point": lg.get("point"),
                      "fair": lg.get("fair"), "price": p["price"], "book": p["book"], "start": p["start"],
                      "at": datetime.now(timezone.utc).isoformat(), "seen": {}, "h": None})
    save(items)


def prices(ev, it):
    """{bookie short name: (price, last_update)} for this exact selection, main or alt line."""
    import picks
    mk, out = it["mk"].replace("_alternate", ""), {}
    for bk in ev.get("bookmakers") or []:
        if bk.get("key") == "betfair_ex_au":
            continue   # exchange back price isn't a bookie price (commission, liquidity)
        for m in bk.get("markets") or []:
            if m.get("key") not in (mk, mk + "_alternate"):
                continue
            for o in m.get("outcomes") or []:
                if (o.get("description") or "") == it["desc"] and o.get("name") == it["side"] and o.get("point") == it["point"]:
                    name = picks.SHORT.get(bk["key"], bk.get("title") or bk["key"])
                    if o["price"] > out.get(name, (0,))[0]:
                        out[name] = (o["price"], m.get("last_update") or bk.get("last_update"))
    return out


def embed(it, now_px, now, final=False):
    alerted, fair = float(it["price"]), it.get("fair")
    rows = sorted(now_px.items(), key=lambda x: -x[1][0])
    best = rows[0][1][0] if rows else None
    lines = []
    for i, (bk, (px, _)) in enumerate(rows[:MAX_BOOKS]):
        was = it["seen"].get(bk)
        move = "" if was is None or abs(px - was) < 0.005 else (" ⬆️" if px > was else " ⬇️")
        star = "**" if i == 0 else ""
        mark = " 💰" if fair and px > float(fair) else ""
        lines.append(f"{star}{bk} ${px:.2f}{star}{move}{mark}")
    gone = [bk for bk in it["seen"] if bk not in now_px]
    if gone:
        lines.append(f"-# Pulled / suspended: {', '.join(gone[:6])}")
    if not rows:
        lines.append("Market's off at every bookie right now (suspended or pulled).")
    if best is None:
        head, colour = "⚠️ No prices right now", GREY
    elif fair and best > float(fair):
        head, colour = f"✅ Still value · best ${best:.2f} vs fair ${float(fair):.2f}", GREEN
    elif fair:
        head, colour = f"❌ No longer value · best ${best:.2f}, fair is ${float(fair):.2f}", RED
    else:
        head, colour = f"Best ${best:.2f}", GREEN if best >= alerted else RED
    diff = "" if best is None else f" · now {'+' if best >= alerted else ''}{best - alerted:.2f}"
    when = now.astimezone(MEL).strftime("%-I:%M%p").lower()
    foot = (f"Closed at the start · {when}" if final else f"Updated {when} · refreshes every 5 min (props every 30)") \
        + f" · alerted ${alerted:.2f} {it['book']}{diff}"
    title = ("🔒 Prices at the start" if final else "📊 Live prices") + " · 💰 = above fair"
    return {"title": title, "description": head + "\n\n" + "\n".join(lines), "color": colour, "footer": {"text": foot[:2000]}}


def edit(it, emb):
    ok = False
    for cid, mid in list(it["msgs"]):
        try:
            discord_post._call("PATCH", f"/channels/{cid}/messages/{mid}", {"embeds": [emb]})
            ok = True
        except Exception as e:
            if "404" in str(e):   # message deleted: stop tracking it
                it["msgs"].remove([cid, mid])
            print(f"::notice::live prices: couldn't edit {mid}: {type(e).__name__} {e}")
    return ok


def main():
    if not (KEY and discord_post.enabled()):
        print("live prices: ODDS_ALERTS_KEY / Discord secrets not set, skipping")
        return
    items = load()
    if not items:
        print("live prices: nothing to track")
        return
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import bet_log
    data = bet_log.load_data()
    evs = {}
    for sec in ("odds", "props"):
        for e in ((data.get(sec) or {}).get("events") or []):
            evs[e["id"]] = e
    if not evs:
        print("live prices: no odds data this run")
        return
    now, keep, edited = datetime.now(timezone.utc), [], 0
    for it in items:
        start = _parse(it["start"])
        if now - start > timedelta(hours=2) or not it["msgs"]:
            continue   # done (locked at the start last run, or every message gone)
        final = now >= start
        if final and it.get("locked"):
            continue
        ev = evs.get(it["ev"])
        if not ev and not final:
            keep.append(it)   # this run didn't pull that sport: try next run
            continue
        px = prices(ev, it) if ev else {}
        if final and not px:
            px = {k: (v, None) for k, v in it["seen"].items()}   # lock on the last prices we saw
        emb = embed(it, px, now, final)
        h = hashlib.sha1(json.dumps([emb["description"], emb["title"]], sort_keys=True).encode()).hexdigest()[:10]
        stale = now - _parse(it.get("t") or it["at"]) >= timedelta(minutes=15)   # keep "Updated" fresh even when nothing moved
        if h != it.get("h") or final or stale:
            if edit(it, emb):
                edited += 1
            it["h"], it["t"] = h, now.isoformat()
        if px:
            it["seen"] = {k: v[0] for k, v in px.items()}
        if final:
            it["locked"] = True
        keep.append(it)
    save(keep)
    print(f"live prices: {edited} alerts edited, {len(keep)} tracked")


if __name__ == "__main__":
    main()
