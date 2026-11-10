"""Australian horse racing win markets from the Betfair Exchange API (delayed app key).

Used by fetch_odds.py: get_racing(prev_racing, now_iso) -> dict for the encrypted bundle.
Needs env BETFAIR_APP_KEY, BETFAIR_USERNAME, BETFAIR_PASSWORD. Read-only: never places bets.
"""
import json, os, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

APP_KEY = os.environ.get("BETFAIR_APP_KEY", "").strip()
USER = os.environ.get("BETFAIR_USERNAME", "").strip()
PW = os.environ.get("BETFAIR_PASSWORD", "")
LOGIN_URLS = ["https://identitysso.betfair.com.au/api/login", "https://identitysso.betfair.com/api/login"]
API_HOSTS = ["https://api.betfair.com.au", "https://api.betfair.com"]
HOURS_AHEAD = float(os.environ.get("RACING_HOURS_AHEAD", "20"))
UA = "overlay-tips-racing/1.0"


def enabled():
    # paused: Betfair refuses logins from GitHub's overseas servers (HTTP 403); avoid repeated failed logins
    return os.environ.get("RACING_ENABLED") == "1" and bool(APP_KEY and USER and PW)


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def login(errors):
    body = urllib.parse.urlencode({"username": USER, "password": PW}).encode()
    for url in LOGIN_URLS:
        try:
            r = _post(url, body, {"X-Application": APP_KEY, "Accept": "application/json",
                                  "Content-Type": "application/x-www-form-urlencoded"})
        except Exception as e:
            errors.append(f"betfair login {url.split('/')[2]}: {type(e).__name__} {e}")
            continue
        if r.get("status") == "SUCCESS" and r.get("token"):
            return r["token"]
        errors.append(f"betfair login {url.split('/')[2]}: {r.get('status')} {r.get('error')}")
    return None


def _rpc(host, token, method, params):
    url = f"{host}/exchange/betting/rest/v1.0/{method}/"
    return _post(url, json.dumps(params).encode(), {
        "X-Application": APP_KEY, "X-Authentication": token,
        "Content-Type": "application/json", "Accept": "application/json"})


def _mid(back, lay):
    if back and lay:
        return (back + lay) / 2
    return back or lay


def fetch_raw(errors):
    """Login + pull AU win markets. Returns {"cat":[...], "books":{...}} or None. Must run from an Australian IP."""
    token = login(errors)
    if not token:
        return None
    now = datetime.now(timezone.utc)
    flt = {"filter": {"eventTypeIds": ["7"], "marketCountries": ["AU"], "marketTypeCodes": ["WIN"],
                      "marketStartTime": {"from": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                          "to": (now + timedelta(hours=HOURS_AHEAD)).strftime("%Y-%m-%dT%H:%M:%SZ")}},
           "marketProjection": ["RUNNER_DESCRIPTION", "EVENT", "MARKET_START_TIME"],
           "sort": "FIRST_TO_START", "maxResults": 200}
    cat, host = None, None
    for h in API_HOSTS:
        try:
            cat = _rpc(h, token, "listMarketCatalogue", flt)
            host = h
            break
        except urllib.error.HTTPError as e:
            errors.append(f"betfair catalogue {h.split('/')[2]}: HTTP {e.code} {e.read()[:150].decode(errors='ignore')}")
        except Exception as e:
            errors.append(f"betfair catalogue {h.split('/')[2]}: {type(e).__name__} {e}")
    if not cat:
        return None
    books = {}
    ids = [m["marketId"] for m in cat]
    for i in range(0, len(ids), 40):
        try:
            for b in _rpc(host, token, "listMarketBook", {"marketIds": ids[i:i + 40],
                                                          "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}}):
                books[b["marketId"]] = b
        except Exception as e:
            errors.append(f"betfair book: {type(e).__name__} {e}")
    return {"fetched": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "cat": cat, "books": books}


def process(raw, prev, ts, errors=None):
    """Raw Betfair data -> {"updated","errors","races","history"} for the encrypted bundle."""
    errors = errors if errors is not None else []
    out = {"updated": (raw or {}).get("fetched", ts), "errors": errors, "races": [],
           "history": (prev or {}).get("history", {})}
    if not raw:
        return out
    cat, books = raw["cat"], raw["books"]
    hist = out["history"]
    keep = set()
    hts = raw.get("fetched", ts)
    for m in cat:
        b = books.get(m["marketId"])
        if not b:
            continue
        ev = m.get("event", {})
        rb = {r["selectionId"]: r for r in b.get("runners", [])}
        runners = []
        for rd in m.get("runners", []):
            r = rb.get(rd["selectionId"], {})
            if r.get("status") != "ACTIVE":
                continue
            ex = r.get("ex", {})
            back = (ex.get("availableToBack") or [{}])[0].get("price")
            lay = (ex.get("availableToLay") or [{}])[0].get("price")
            runners.append({"id": rd["selectionId"], "name": rd.get("runnerName", ""),
                            "no": (rd.get("metadata") or {}).get("CLOTH_NUMBER"),
                            "back": back, "lay": lay, "ltp": r.get("lastPriceTraded")})
        # fair: mid of back/lay, normalised so the field adds to 100%
        mids = {x["id"]: _mid(x["back"], x["lay"]) or x["ltp"] for x in runners}
        tot = sum(1 / v for v in mids.values() if v and v > 1)
        for x in runners:
            v = mids.get(x["id"])
            x["fair"] = round(1 / ((1 / v) / tot), 2) if v and v > 1 and tot else None
            key = f"{m['marketId']}|{x['id']}"
            keep.add(key)
            if x["fair"]:
                ser = hist.setdefault(key, [])
                if not ser or ser[-1][1] != x["fair"]:
                    ser.append([hts, x["fair"]])
                ser[:] = ser[:1] + ser[1:][-23:]
        runners.sort(key=lambda x: x["fair"] or 999)
        out["races"].append({"id": m["marketId"], "start": m.get("marketStartTime"), "name": m.get("marketName", ""),
                             "venue": ev.get("venue") or ev.get("name", ""), "matched": round(b.get("totalMatched", 0)),
                             "status": b.get("status"), "runners": runners})
    out["history"] = {k: v for k, v in hist.items() if k in keep}
    return out


if __name__ == "__main__":
    # Run on a computer in Australia: python3 fetch_racing.py OUT.json
    # Reads BETFAIR_* from env (or config.json next to OUT), encrypts the raw pull with ODDS_ALERTS_KEY.
    import sys, gzip, base64, secrets
    outp = sys.argv[1]
    cfgp = os.path.join(os.path.dirname(os.path.abspath(outp)), "config.json")
    if os.path.exists(cfgp):
        cfg = json.load(open(cfgp))
        APP_KEY, USER, PW = cfg["BETFAIR_APP_KEY"].strip(), cfg["BETFAIR_USERNAME"].strip(), cfg["BETFAIR_PASSWORD"]
        key = cfg["ODDS_ALERTS_KEY"]
    else:
        key = os.environ["ODDS_ALERTS_KEY"]
    errs = []
    raw = fetch_raw(errs)
    if not raw:
        print("FAILED:", errs); sys.exit(1)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = secrets.token_bytes(12)
    ct = AESGCM(base64.b64decode(key)).encrypt(iv, gzip.compress(json.dumps(raw).encode()), None)
    json.dump({"v": 1, "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}, open(outp, "w"))
    print(f"OK {len(raw['cat'])} races -> {outp}")
