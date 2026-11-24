"""Gemini reads today's racing on demand: the model's top picks for today that haven't raced yet.

One Google-search-grounded Gemini call over all of them (form, track/distance, barrier/box, track condition,
scratchings, gear), a BACK or PASS on each at its rated price, sent to Jayden's ntfy channel.
Run from GitHub: Actions -> "Racing read" -> Run workflow. Flag only: nothing gets bet or posted to members.

Env: ODDS_ALERTS_KEY (decrypts picks.json + racing_log.json), GEMINI_API_KEY, NTFY_TOPIC. OUT = odds-data dir.
"""
import json, os, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gemini, picks, racing_log, value_alerts

SYD = ZoneInfo("Australia/Sydney")
OUT = os.environ.get("OUT", "odds-data")
VERDICT = {"back": "✅ BACK", "pass": "❌ PASS", "noread": "❔ No read"}


def main():
    key = os.environ.get("ODDS_ALERTS_KEY", "").strip()
    if not (key and gemini.enabled()):
        sys.exit("ODDS_ALERTS_KEY / GEMINI_API_KEY not set")
    now = datetime.now(timezone.utc)
    day = now.astimezone(SYD).date().isoformat()
    bundle = json.load(open(os.path.join(OUT, "picks.json")))
    tops = (picks.decrypt_with_key(bundle, key) or {}).get("race_tops") or {}
    if tops.get("date") != day or not tops.get("items"):
        print(f"no model top picks for {day}")
        value_alerts._push(value_alerts.TOPIC, "🏇 Racing read", ["No model top picks for today."], priority=2)
        return
    # skip races already run (the racing log has a result for them)
    done = set()
    try:
        for p in racing_log.load_log().get("picks") or []:
            if p.get("result") and p.get("date") == day:
                done.add((str(p.get("mid")), str(p.get("sid"))))
    except Exception as e:
        print(f"::warning::racing log: {e}")
    todo = [x for x in tops["items"] if (racing_log._mid(x["mid"]), str(x["sid"])) not in done]
    if not todo:
        value_alerts._push(value_alerts.TOPIC, "🏇 Racing read", ["All of today's model top picks have already run."], priority=2)
        return
    runs = [{"runner": x["name"], "no": x.get("no"), "kind": x["kind"], "track": x["venue"], "race": x.get("num"),
             "model_rated_price": x["rated"], "next_best": f"{x['next']} (${x['next_rated']:.2f})"} for x in todo]
    out = gemini.ask(
        f"Today is {now.astimezone(SYD).strftime('%A %-d %B %Y')} in Australia. These are a ratings model's top picks "
        "in today's races, each with the model's rated price. For each, search the latest form guide and race news "
        "(last few starts, track/distance record, barrier/box, today's track condition, class change, gear or jockey "
        "changes, scratchings) and make the call a sharp punter would: BACK if you'd take a price at or above the "
        "rated one, PASS if the form gives a real reason against. Don't pass everything. Only state what a source "
        "says. If you can't find a runner's form, the verdict is \"noread\" (not a pass: no info isn't a reason against).\n"
        "Reply as JSON only: {\"calls\": [{\"runner\": name exactly as given, \"verdict\": \"back\"|\"pass\"|\"noread\", "
        "\"why\": one short sentence, casual Aussie punter voice, no emojis}]}\n\n" + json.dumps(runs),
        json_out=True, search=True, max_tokens=1500, label="racing read")
    calls = {str(c.get("runner", "")).lower(): c for c in (out or {}).get("calls") or []}
    if not calls:
        sys.exit("Gemini gave no calls (check the log above)")
    lines = []
    for kind, emo in (("Horse", "🏇"), ("Greyhound", "🐕")):
        xs = [x for x in todo if x["kind"] == kind]
        if xs:
            lines += ["", f"{emo} {'Horses' if kind == 'Horse' else 'Greyhounds'}"]
        for x in xs:
            c = calls.get(x["name"].lower()) or {}
            v = VERDICT.get(str(c.get("verdict", "")).lower(), "❔ no call")
            lines.append(f"{x['venue']} R{x.get('num')}: {(str(x['no']) + '. ') if x.get('no') else ''}{x['name']} "
                         f"(rated ${x['rated']:.2f}) · {v}" + (f" · {c['why']}" if c.get("why") else ""))
    lines += ["", "Gemini's read of a Google search of the form, not a sure thing. Only back at or above the rated price."]
    print("\n".join(lines))
    value_alerts._push(value_alerts.TOPIC, f"🤖 Gemini's racing read · {now.astimezone(SYD).strftime('%a %-d %b')}",
                       lines[1:], click=value_alerts.SITE + "/odds.html", priority=3)


if __name__ == "__main__":
    main()
