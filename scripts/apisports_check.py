import json, os, urllib.request
K=os.environ["APISPORTS_KEY"].strip()
for path in ["/status", "/games?league=1&season=2026-2027&date=2026-11-13&timezone=Australia/Sydney"]:
    req=urllib.request.Request("https://v1.basketball.api-sports.io"+path,headers={"x-apisports-key":K,"User-Agent":"x"})
    with urllib.request.urlopen(req) as r:
        h={k:v for k,v in r.headers.items() if "ratelimit" in k.lower() or "requests" in k.lower()}
        j=json.load(r)
    print(path, h, json.dumps(j.get("errors")), json.dumps(j.get("response"))[:300] if path=="/status" else [(g["teams"]["home"]["name"],g["status"]["short"],g["scores"]["home"]["total"],g["scores"]["away"]["total"]) for g in j.get("response",[])])
