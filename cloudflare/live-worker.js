// Overlay Tips: live NBA scores for the members' Live page (overlay.tips/live.html).
//
// Runs as a Cloudflare Worker so the BALLDONTLIE key stays secret: the page never sees it.
// It only answers overlay.tips, and only with the members password, so it isn't an open data feed
// (BALLDONTLIE's terms don't allow re-serving their data as a public feed or sharing the key).
//
// Cloudflare settings (Worker -> Settings -> Variables and Secrets), both as "Secret":
//   BDL_KEY        your BALLDONTLIE API key
//   LIVE_PASSWORD  the members password (same one as the Odds board)
//   APISPORTS_KEY  your API-Sports key (NBL live scores; optional)
//
// GET /nba?dates=YYYY-MM-DD,YYYY-MM-DD   (US dates, up to 3)  header X-Members-Password
// -> {"updated", "games": [{"id", "start", "state": "pre"|"in"|"post", "status", "period", "clock",
//                           "home": TEAM, "away": TEAM}]}
//    TEAM = {"abbr", "name", "score", "players": [{"name", "min", "pts", "reb", "ast", "fg3m",
//                                                  "stl", "blk", "tov", "pf", "fgm", "fga", "fg3a",
//                                                  "ftm", "fta", "oreb", "dreb", "pm"}]}
// GET /nbl?dates=YYYY-MM-DD,...   (Australian dates, up to 3)  header X-Members-Password
// -> same shape as /nba, with "lg": "NBL" and ids like "nbl501238". Needs the APISPORTS_KEY secret
//    (API-Sports Pro). No steals/blocks/turnovers/play-by-play for the NBL.
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

// ---------------- NBL (API-Sports / API-Basketball) ----------------
const AS = "https://v1.basketball.api-sports.io";
const NBL_ABBR = { "Adelaide": "ADL", "Brisbane": "BRI", "Cairns": "CNS", "Illawarra": "ILL", "Melbourne": "MEL",
  "New Zealand": "NZB", "Perth": "PER", "South East Melbourne": "SEM", "Sydney": "SYD", "Tasmania": "TAS" };
const nblAbbr = (n) => {
  const k = Object.keys(NBL_ABBR).filter((k) => String(n || "").startsWith(k)).sort((a, b) => b.length - a.length)[0];
  return k ? NBL_ABBR[k] : String(n || "").slice(0, 3).toUpperCase();
};

// The Cache API does nothing on *.workers.dev, so NBL answers are also kept in memory (per Worker
// instance): the season list for 5 min, a full answer for 30s, and after an API-Sports error we
// wait 90s before asking again.
const MEM = { season: null, seasonAt: 0, answers: {}, backoffUntil: 0, lastErr: "" };

async function as(env, path) {
  if (Date.now() < MEM.backoffUntil) throw new Error("waiting after API-Sports limit: " + MEM.lastErr);
  const r = await fetch(AS + path, { headers: { "x-apisports-key": env.APISPORTS_KEY } });
  const left = r.headers.get("x-ratelimit-remaining"), perMin = r.headers.get("x-ratelimit-limit");
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  const j = await r.json();
  if (j.errors && Object.keys(j.errors).length) {
    MEM.backoffUntil = Date.now() + 90e3;
    MEM.lastErr = JSON.stringify(j.errors).slice(0, 120) + (perMin ? ` (limit ${perMin}/min, ${left} left)` : "");
    throw new Error(MEM.lastErr);
  }
  return j.response || [];
}

async function nbl(url, env, ctx, cors) {
  if (!env.APISPORTS_KEY) return json({ updated: new Date().toISOString(), games: [], note: "APISPORTS_KEY not set" }, 200, cors);
  const dates = (url.searchParams.get("dates") || "").split(",").filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d)).slice(0, 3);
  if (!dates.length) return json({ error: "dates" }, 400, cors);
  const cache = caches.default;
  const key = new Request("https://overlay-live.cache/nbl?d=" + dates.join(","));
  const mem = MEM.answers[dates.join(",")];
  if (mem && Date.now() - mem.at < 30e3)
    return new Response(mem.body, { status: mem.status, headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } });
  const hit = await cache.match(key);
  if (hit) return new Response(hit.body, { status: hit.status, headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } });
  let body;
  try {
    const now = new Date(), y = now.getUTCMonth() >= 6 ? now.getUTCFullYear() : now.getUTCFullYear() - 1;
    const season = `${y}-${y + 1}`;
    // one call for the whole season (cached 2 min), filtered to the asked-for Australian dates
    const skey = new Request("https://overlay-live.cache/nbl-season?s=" + season);
    let all = MEM.season && Date.now() - MEM.seasonAt < 300e3 ? MEM.season : null;
    const sh = all ? null : await cache.match(skey);
    if (all) {}
    else if (sh) all = await sh.json();
    else {
      all = await as(env, `/games?league=1&season=${season}&timezone=Australia/Sydney`);
      MEM.season = all; MEM.seasonAt = Date.now();
      ctx.waitUntil(cache.put(skey, new Response(JSON.stringify(all), {
        headers: { "Content-Type": "application/json", "Cache-Control": "max-age=120" } })));
    }
    const auDate = (iso) => new Date(iso).toLocaleDateString("en-CA", { timeZone: "Australia/Sydney" });
    let games = all.filter((g) => dates.includes(auDate(g.date)));
    // games in progress: the season list is up to 2 min old, so refresh just today's games (1 call)
    if (games.some((g) => !["NS", "FT", "AOT", "POST", "CANC"].includes(g.status && g.status.short))
        || games.some((g) => g.status && g.status.short === "NS" && Date.parse(g.date) < Date.now())) {
      const ids = new Set(games.map((g) => g.id));
      const today = auDate(new Date().toISOString());
      const fresh = await as(env, `/games?league=1&season=${season}&date=${today}&timezone=Australia/Sydney`);
      const byId = Object.fromEntries(fresh.map((g) => [g.id, g]));
      games = games.map((g) => byId[g.id] || g).concat(fresh.filter((g) => !ids.has(g.id) && dates.includes(auDate(g.date))));
    }
    // box scores only for games on now or finished in the last 6 hours
    const started = games.filter((g) => !["NS", "CANC", "POST"].includes(g.status && g.status.short)
      && Date.now() - Date.parse(g.date) < 9 * 3600e3).map((g) => g.id);
    const rows = started.length ? await as(env, `/games/statistics/players?ids=${started.join("-")}`) : [];
    const byTeam = {};
    for (const r of rows) (byTeam[`${r.game && r.game.id}|${r.team && r.team.id}`] ||= []).push(r);
    const side = (g, t, score) => ({
      abbr: nblAbbr(t.name), name: t.name, score: score ?? null,
      players: (byTeam[`${g.id}|${t.id}`] || []).map((p) => ({
        name: (p.player && p.player.name) || "", min: p.minutes || "", pts: p.points ?? 0,
        reb: (p.rebounds && p.rebounds.total) ?? 0, ast: p.assists ?? 0,
        fg3m: (p.threepoint_goals && p.threepoint_goals.total) ?? 0, fg3a: (p.threepoint_goals && p.threepoint_goals.attempts) ?? 0,
        ftm: (p.freethrows_goals && p.freethrows_goals.total) ?? 0, fta: (p.freethrows_goals && p.freethrows_goals.attempts) ?? 0,
        stl: 0, blk: 0, tov: 0, pf: 0, fgm: 0, fga: 0, oreb: 0, dreb: 0, pm: null, starter: p.type === "starters",
      })),
    });
    const per = { Q1: 1, Q2: 2, HT: 2, Q3: 3, Q4: 4, BT: 0, OT: 5 };
    const out = games.map((g) => {
      const st = (g.status && g.status.short) || "";
      const state = st === "NS" ? "pre" : ["FT", "AOT", "POST", "CANC"].includes(st) ? "post" : "in";
      return {
        id: "nbl" + g.id, lg: "NBL", start: g.date, state, status: (g.status && g.status.long) || st,
        period: per[st] ?? 0, clock: st === "HT" ? "Half" : g.status && g.status.timer ? `${g.status.timer}'` : "",
        home: side(g, g.teams.home, g.scores && g.scores.home && g.scores.home.total),
        away: side(g, g.teams.away, g.scores && g.scores.away && g.scores.away.total),
      };
    });
    body = JSON.stringify({ updated: new Date().toISOString(), games: out });
  } catch (e) {
    const err = JSON.stringify({ error: "upstream", detail: String(e && e.message || e).slice(0, 200) });
    MEM.answers[dates.join(",")] = { at: Date.now(), status: 502, body: err };
    ctx.waitUntil(cache.put(key, new Response(err, {   // don't retry API-Sports for a minute after an error
      status: 502, headers: { "Content-Type": "application/json", "Cache-Control": "max-age=60" } })));
    return new Response(err, { status: 502, headers: { ...cors, "Content-Type": "application/json" } });
  }
  MEM.answers[dates.join(",")] = { at: Date.now(), status: 200, body };
  ctx.waitUntil(cache.put(key, new Response(body, {
    headers: { "Content-Type": "application/json", "Cache-Control": `max-age=${CACHE_SECONDS}` },
  })));
  return new Response(body, { headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } });
}
