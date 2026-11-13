// Overlay Tips: live NBA scores for the members' Live page (overlay.tips/live.html).
//
// Runs as a Cloudflare Worker so the BALLDONTLIE key stays secret: the page never sees it.
// It only answers overlay.tips, and only with the members password, so it isn't an open data feed
// (BALLDONTLIE's terms don't allow re-serving their data as a public feed or sharing the key).
//
// Cloudflare settings (Worker -> Settings -> Variables and Secrets), both as "Secret":
//   BDL_KEY        your BALLDONTLIE API key
//   LIVE_PASSWORD  the members password (same one as the Odds board)
//
// GET /nba?dates=YYYY-MM-DD,YYYY-MM-DD   (US dates, up to 3)  header X-Members-Password
// -> {"updated", "games": [{"id", "start", "state": "pre"|"in"|"post", "status", "period", "clock",
//                           "home": TEAM, "away": TEAM}]}
//    TEAM = {"abbr", "name", "score", "players": [{"name", "min", "pts", "reb", "ast", "fg3m",
//                                                  "stl", "blk", "tov", "pf", "fgm", "fga", "fg3a",
//                                                  "ftm", "fta", "oreb", "dreb", "pm"}]}
// GET /nbl?dates=YYYY-MM-DD,...   (Australian dates, up to 3)  header X-Members-Password
// -> same shape as /nba, with "lg": "NBL" and ids like "nbl501238", from the encrypted feed the
//    "NBL live" GitHub Action publishes. No steals/blocks/turnovers/play-by-play for the NBL.
// GET /plays?game_id=N   header X-Members-Password   (play-by-play, BALLDONTLIE GOAT tier)
// -> {"updated", "game_id", "plays": [{"n", "period", "clock", "text", "team", "home", "away",
//                                      "scoring", "value"}]}   oldest first
// Answers are cached for 20 seconds (plays 10s), so BALLDONTLIE sees at most a few calls a minute.

const BDL = "https://api.balldontlie.io/v1";
const ORIGINS = ["https://overlay.tips", "https://www.overlay.tips"];
const CACHE_SECONDS = 20;
const PLAYS_CACHE_SECONDS = 10;

export default {
  async fetch(req, env, ctx) {
    const origin = req.headers.get("Origin") || "";
    const cors = {
      "Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
      "Access-Control-Allow-Headers": "X-Members-Password",
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Vary": "Origin",
    };
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (!ORIGINS.includes(origin)) return json({ error: "forbidden" }, 403, cors);
    if (!env.BDL_KEY || !env.LIVE_PASSWORD) return json({ error: "not set up" }, 500, cors);
    if (!(await same(req.headers.get("X-Members-Password") || "", env.LIVE_PASSWORD)))
      return json({ error: "password" }, 401, cors);

    const url = new URL(req.url);
    if (url.pathname === "/plays") return plays(url, env, ctx, cors);
    if (url.pathname === "/nbl") return nbl(url, env, ctx, cors);
    if (url.pathname !== "/nba") return json({ error: "not found" }, 404, cors);
    const dates = (url.searchParams.get("dates") || "").split(",")
      .filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d)).slice(0, 3);
    if (!dates.length) return json({ error: "dates" }, 400, cors);

    const cache = caches.default;
    const key = new Request("https://overlay-live.cache/nba?d=" + dates.join(","));
    const hit = await cache.match(key);
    if (hit) return new Response(hit.body, { headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } });

    let body;
    try {
      body = JSON.stringify(await build(env, dates));
    } catch (e) {
      return json({ error: "upstream", detail: String(e && e.message || e).slice(0, 200) }, 502, cors);
    }
    ctx.waitUntil(cache.put(key, new Response(body, {
      headers: { "Content-Type": "application/json", "Cache-Control": `max-age=${CACHE_SECONDS}` },
    })));
    return new Response(body, { headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } });
  },
};

async function plays(url, env, ctx, cors) {
  const id = url.searchParams.get("game_id") || "";
  if (!/^\d{1,12}$/.test(id)) return json({ error: "game_id" }, 400, cors);
  const cache = caches.default;
  const key = new Request("https://overlay-live.cache/plays?g=" + id);
  const hit = await cache.match(key);
  if (hit) return new Response(hit.body, { headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } });
  let body;
  try {
    const all = [];
    let cursor = null;
    for (let page = 0; page < 10; page++) {   // a full game is a few hundred plays
      const qs = `game_id=${id}&per_page=100${cursor != null ? "&cursor=" + encodeURIComponent(cursor) : ""}`;
      const r = await fetch(`${BDL}/plays?${qs}`, { headers: { Authorization: env.BDL_KEY } });
      if (!r.ok) throw new Error(`/plays ${r.status}`);
      const j = await r.json();
      all.push(...(j.data || []));
      cursor = j.meta && j.meta.next_cursor;
      if (cursor == null) break;
    }
    all.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    body = JSON.stringify({
      updated: new Date().toISOString(), game_id: Number(id),
      plays: all.map((p) => ({
        n: p.order ?? 0, period: p.period ?? 0, clock: p.clock ?? "", text: p.text || "",
        team: (p.team && p.team.abbreviation) || "", home: p.home_score ?? null, away: p.away_score ?? null,
        scoring: !!p.scoring_play, value: p.score_value ?? 0,
      })),
    });
  } catch (e) {
    return json({ error: "upstream", detail: String(e && e.message || e).slice(0, 200) }, 502, cors);
  }
  ctx.waitUntil(cache.put(key, new Response(body, {
    headers: { "Content-Type": "application/json", "Cache-Control": `max-age=${PLAYS_CACHE_SECONDS}` },
  })));
  return new Response(body, { headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } });
}

function json(obj, status, headers) {
  return new Response(JSON.stringify(obj), { status, headers: { ...headers, "Content-Type": "application/json" } });
}

async function same(a, b) {   // compare without leaking timing
  const enc = new TextEncoder();
  const [x, y] = await Promise.all([a, b].map((s) => crypto.subtle.digest("SHA-256", enc.encode(s))));
  const u = new Uint8Array(x), v = new Uint8Array(y);
  let d = 0;
  for (let i = 0; i < u.length; i++) d |= u[i] ^ v[i];
  return d === 0;
}

async function bdl(env, path, params = []) {
  const qs = params.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
  const r = await fetch(`${BDL}${path}${qs ? "?" + qs : ""}`, { headers: { Authorization: env.BDL_KEY } });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return (await r.json()).data || [];
}

function state(status, period) {
  const s = String(status || "");
  if (/^final/i.test(s)) return "post";
  if (!period || /^\d{4}-\d{2}-\d{2}T/.test(s) || /\b(am|pm|ET)\b/i.test(s)) return "pre";
  return "in";
}

function team(t, score, rows) {
  return {
    abbr: (t && t.abbreviation) || "", name: (t && t.full_name) || "", score: score ?? null,
    players: (rows || []).map((p) => ({
      name: `${(p.player && p.player.first_name) || ""} ${(p.player && p.player.last_name) || ""}`.trim(),
      min: p.min ?? "", pts: p.pts ?? 0, reb: p.reb ?? 0, ast: p.ast ?? 0, fg3m: p.fg3m ?? 0,
      stl: p.stl ?? 0, blk: p.blk ?? 0, tov: p.turnover ?? 0, pf: p.pf ?? 0,
      fgm: p.fgm ?? 0, fga: p.fga ?? 0, fg3a: p.fg3a ?? 0, ftm: p.ftm ?? 0, fta: p.fta ?? 0,
      oreb: p.oreb ?? 0, dreb: p.dreb ?? 0, pm: p.plus_minus ?? null,
    })),
  };
}

async function build(env, dates) {
  const [games, live, ...byDate] = await Promise.all([
    bdl(env, "/games", dates.map((d) => ["dates[]", d]).concat([["per_page", 100]])),
    bdl(env, "/box_scores/live").catch(() => []),
    ...dates.map((d) => bdl(env, "/box_scores", [["date", d]]).catch(() => [])),
  ]);
  // box scores don't carry the game id: key them by date + home team
  const boxes = {};
  for (const b of byDate.flat().concat(live)) {   // live last, so it wins
    const k = `${String(b.date || "").slice(0, 10)}|${b.home_team && b.home_team.id}`;
    boxes[k] = b;
  }
  const out = games.map((g) => {
    const b = boxes[`${String(g.date || "").slice(0, 10)}|${g.home_team && g.home_team.id}`] || null;
    const status = (b && b.status) || g.status, period = (b && b.period) ?? g.period;
    return {
      id: g.id, start: g.datetime || (/^\d{4}-\d{2}-\d{2}T/.test(String(g.status)) ? g.status : g.date),
      state: state(status, period), status: String(status || ""), period: period || 0, clock: (b && b.time) || g.time || "",
      home: team(g.home_team, (b && b.home_team_score) ?? g.home_team_score, b && b.home_team && b.home_team.players),
      away: team(g.visitor_team, (b && b.visitor_team_score) ?? g.visitor_team_score, b && b.visitor_team && b.visitor_team.players),
    };
  });
  out.sort((a, b) => String(a.start).localeCompare(String(b.start)));
  return { updated: new Date().toISOString(), games: out };
}

// ---------------- NBL ----------------
// API-Sports blocks Cloudflare's network, so a GitHub Action ("NBL live") fetches the NBL scoreboard every
// 30s during games and publishes it encrypted to the nbl-live branch. This decrypts it for members
// (AES-GCM, key = SHA-256("nbl-live:" + LIVE_PASSWORD)) and keeps it in memory for 15s.
const NBL_SRC = "https://raw.githubusercontent.com/OverlayTips/Overlay_Tips/nbl-live/nbl.json";
const MEM = { at: 0, data: null };

async function nblFeed(env) {
  if (MEM.data && Date.now() - MEM.at < 15e3) return MEM.data;
  // raw.githubusercontent caches each path for 5 min, so the feed also writes s/<unix time // 30>.json every
  // 30s. Read the newest finished slot; fall back to the fixed file if none of the recent ones exist.
  const now = Date.now() / 1e3, slot = Math.floor(now / 30), first = now % 30 > 12 ? 1 : 2;
  let r = null;
  for (let k = first; k < first + 4 && !(r && r.ok); k++)
    r = await fetch(NBL_SRC.replace("nbl.json", `s/${slot - k}.json`), { cf: { cacheTtl: 0 } });
  if (!r || !r.ok) r = await fetch(`${NBL_SRC}?t=${Math.floor(Date.now() / 15e3)}`, { cf: { cacheTtl: 0 } });
  if (!r.ok) throw new Error(`NBL feed ${r.status}`);
  const b = await r.json();
  const b64 = (x) => Uint8Array.from(atob(x), (c) => c.charCodeAt(0));
  const raw = await crypto.subtle.digest("SHA-256", new TextEncoder().encode("nbl-live:" + env.LIVE_PASSWORD));
  const key = await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["decrypt"]);
  let pt;
  try {
    pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64(b.iv) }, key, b64(b.ct));
  } catch (e) {
    throw new Error("NBL feed: LIVE_PASSWORD in GitHub secrets doesn't match Cloudflare");
  }
  MEM.data = JSON.parse(new TextDecoder().decode(pt));
  MEM.at = Date.now();
  return MEM.data;
}

async function nbl(url, env, ctx, cors) {
  const dates = (url.searchParams.get("dates") || "").split(",").filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d)).slice(0, 3);
  if (!dates.length) return json({ error: "dates" }, 400, cors);
  try {
    const d = await nblFeed(env);
    const auDate = (iso) => new Date(iso).toLocaleDateString("en-CA", { timeZone: "Australia/Sydney" });
    const games = (d.games || []).filter((g) => dates.includes(auDate(g.start)));
    return json({ updated: d.updated, games }, 200, { ...cors, "Cache-Control": "no-store" });
  } catch (e) {
    return json({ error: "upstream", detail: String(e && e.message || e).slice(0, 200) }, 502, cors);
  }
}
