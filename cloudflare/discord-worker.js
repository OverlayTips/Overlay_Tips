// Overlay Tips: the /stats and /why commands in the Overlay Discord.
//   /stats Zion        -> his live NBA line
//   /why Jalen Brunson -> Gemini explains today's pick(s) on that player, from the same numbers the model used
//                         (the public Hot stats feed: hit rates, game log, matchup, minutes, prices)
//
// Runs as a Cloudflare Worker that Discord calls (Developer Portal -> your app -> General Information ->
// Interactions Endpoint URL = this Worker's URL). Replies are only shown to the member who asked
// (ephemeral), and only members with a Founder / Daily Card / VIP role can use it, so BALLDONTLIE's data
// isn't re-served as a public feed.
//
// Cloudflare settings (Worker -> Settings -> Variables and Secrets):
//   BDL_KEY             (Secret) your BALLDONTLIE API key, same as the overlay-live Worker
//   DISCORD_PUBLIC_KEY  (Secret) Developer Portal -> General Information -> Public Key
//   GEMINI_API_KEY      (Secret) Google AI Studio key, for /why (same key as the GitHub secret is fine)
//   ALLOWED_ROLE_IDS    (Text)   comma-separated role ids allowed to use /stats and /why (the Discord setup
//                                workflow prints them). Leave empty to allow everyone in the server.
// The command itself is registered by scripts/discord_commands.py (Discord setup workflow).

const BDL = "https://api.balldontlie.io/v1";
const API = "https://discord.com/api/v10";
const FOOT = "-# Live from the Overlay Tips tracker · 18+ · Gamble responsibly · 1800 858 858";

export default {
  async fetch(req, env, ctx) {
    if (req.method !== "POST") return new Response("Overlay Tips Discord bot", { status: 200 });
    const body = await req.text();
    if (!(await verify(req, body, env.DISCORD_PUBLIC_KEY || ""))) return new Response("bad signature", { status: 401 });
    const it = JSON.parse(body);
    if (it.type === 1) return reply({ type: 1 });   // Discord's PING when you save the endpoint URL

    if (it.type === 2 && it.data && (it.data.name === "stats" || it.data.name === "why")) {
      const cmd = it.data.name;
      const allowed = String(env.ALLOWED_ROLE_IDS || "").split(",").map((s) => s.trim()).filter(Boolean);
      const roles = (it.member && it.member.roles) || [];
      if (allowed.length && !roles.some((r) => allowed.includes(r))) {
        return reply({ type: 4, data: { flags: 64, content:
          `🔒 /${cmd} is for Overlay members. Sign up free at <https://overlay.tips/portal.html> and tap **Connect Discord**.` } });
      }
      const q = String(((it.data.options || []).find((o) => o.name === "player") || {}).value || "").trim();
      ctx.waitUntil(answer(it, q, env, cmd));
      return reply({ type: 5, data: { flags: 64 } });   // "thinking…", then answer() edits it
    }
    return reply({ type: 4, data: { flags: 64, content: "Unknown command." } });
  },
};

async function answer(it, q, env, cmd) {
  let content;
  try {
    content = cmd === "why" ? await whyFor(q, env) : await statsFor(q, env);
  } catch (e) {
    content = cmd === "why" ? "Couldn't pull the pick right now. Try again in a minute."
      : "Couldn't get live stats right now. Try again in a minute.";
  }
  await fetch(`${API}/webhooks/${it.application_id}/${it.token}/messages/@original`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: content.slice(0, 1990), allowed_mentions: { parse: [] } }),
  });
}

const norm = (s) => String(s || "").toLowerCase().replace(/[.\-']/g, " ").replace(/[^a-z ]/g, "")
  .split(/\s+/).filter((w) => w && !["jr", "sr", "ii", "iii", "iv"].includes(w)).join(" ");

async function bdl(env, path, params = []) {
  const qs = params.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
  const r = await fetch(`${BDL}${path}${qs ? "?" + qs : ""}`, { headers: { Authorization: env.BDL_KEY } });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return (await r.json()).data || [];
}

function usDate(offsetDays = 0) {   // US Eastern date, as BALLDONTLIE dates games
  const d = new Date(Date.now() + offsetDays * 864e5);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(d);
}

function clock(b) {
  const s = String(b.status || "");
  if (/^final/i.test(s)) return "Final";
  if (!b.period || /^\d{4}-\d{2}-\d{2}T/.test(s) || /\b(am|pm|ET)\b/i.test(s)) return "Not started";
  const per = b.period <= 4 ? `Q${b.period}` : (b.period === 5 ? "OT" : `${b.period - 4}OT`);
  const t = String(b.time || "").trim();
  return t && !/^(final|half)$/i.test(t) ? `${per} ${t}` : (t ? t[0].toUpperCase() + t.slice(1) : per);
}

async function statsFor(q, env) {
  const want = norm(q).split(" ").filter(Boolean);
  if (!want.length) return "Type a player, e.g. `/stats Zion` or `/stats Jalen Brunson`.";
  const today = usDate(0);
  const [live, day] = await Promise.all([
    bdl(env, "/box_scores/live").catch(() => []),
    bdl(env, "/box_scores", [["date", today]]).catch(() => []),
  ]);
  const games = {};
  for (const b of day.concat(live)) games[`${String(b.date || "").slice(0, 10)}|${b.home_team && b.home_team.id}`] = b;
  const hits = [];
  for (const b of Object.values(games)) {
    for (const side of ["home_team", "visitor_team"]) {
      for (const p of (b[side] && b[side].players) || []) {
        const name = `${(p.player && p.player.first_name) || ""} ${(p.player && p.player.last_name) || ""}`.trim();
        const n = norm(name).split(" ");
        if (want.every((w) => n.some((x) => x.startsWith(w)))) hits.push({ b, side, p, name });
      }
    }
  }
  if (!hits.length) {
    return `No NBA game today for **${q}** that we can see (or the game hasn't started yet). ` +
      "Check spelling, or see Hot stats: <https://overlay.tips/hot.html>";
  }
  if (hits.length > 1 && !hits.every((h) => h.name === hits[0].name)) {
    return "A few players match. Try a full name:\n" + [...new Set(hits.map((h) => h.name))].slice(0, 6).map((n) => `• ${n}`).join("\n");
  }
  const { b, side, p, name } = hits[0];
  const team = b[side].abbreviation, home = b.home_team.abbreviation, away = b.visitor_team.abbreviation;
  const score = clock(b) === "Not started" ? "" : ` · ${away} ${b.visitor_team_score ?? 0}-${b.home_team_score ?? 0} ${home}`;
  const n = (k) => p[k] ?? 0;
  return [
    `🏀 **${name}** (${team}) · ${away} @ ${home} · ${clock(b)}${score}`,
    `**${n("pts")} pts · ${n("reb")} reb · ${n("ast")} ast · ${n("fg3m")} 3PM** · ${n("stl")} stl · ${n("blk")} blk · ${n("turnover")} TO`,
    `${n("fgm")}/${n("fga")} FG · ${n("fg3m")}/${n("fg3a")} 3PT · ${n("ftm")}/${n("fta")} FT · ${p.min || 0} min · ${n("pf")} fouls`,
    FOOT,
  ].join("\n");
}

function reply(obj) {
  return new Response(JSON.stringify(obj), { headers: { "Content-Type": "application/json" } });
}

function hex(s) {
  const out = new Uint8Array(s.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.substr(i * 2, 2), 16);
  return out;
}

// ---------------- /why ----------------
const HOT = "https://raw.githubusercontent.com/OverlayTips/Overlay_Tips/odds-data/hot.json";
const GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/";
const WHY_FOOT = "-# Gemini's read of the model's numbers, not a guarantee · 18+ · Gamble responsibly · 1800 858 858";

async function whyFor(q, env) {
  if (!q) return "Tell me who, e.g. `/why Jalen Brunson`.";
  if (!env.GEMINI_API_KEY) return "/why isn't switched on yet.";
  const r = await fetch(HOT, { cf: { cacheTtl: 120 } });
  if (!r.ok) throw new Error("hot " + r.status);
  const hot = await r.json();
  const want = norm(q), last = want.split(" ").pop();
  const pool = [...(hot.value || []).map((x) => ({ ...x, _k: "value pick" })),
    ...(hot.items || []).map((x) => ({ ...x, _k: "hot stat" })),
    ...(hot.cold || []).map((x) => ({ ...x, _k: "cold stat (Under)" }))];
  let hits = pool.filter((x) => norm(x.player) === want);
  if (!hits.length) hits = pool.filter((x) => norm(x.player).includes(want) || norm(x.player).split(" ").pop() === last);
  const names = [...new Set(hits.map((x) => x.player))];
  if (!hits.length) return `No pick on **${q}** in today's feed. Check <https://overlay.tips/hot.html> for who's on it.`;
  if (names.length > 1) return `A few players match: ${names.slice(0, 5).join(", ")}. Try the full name.`;
  // value picks first, then the best hit rate; one line per stat
  const seen = new Set(), picks = [];
  for (const x of hits.sort((a, b) => (a._k === "value pick" ? -1 : 0) - (b._k === "value pick" ? -1 : 0))) {
    const k = x.stat + "|" + (x.side || "Over");
    if (!seen.has(k)) { seen.add(k); picks.push(x); }
  }
  const keep = ["player", "team", "opp", "game", "start", "stat", "line", "side", "price", "book", "l10", "l5", "vs",
    "season", "avg", "prob", "mkt", "value", "games", "h2h", "wo", "proj", "mu", "pace", "moved", "_k"];
  const data = picks.slice(0, 3).map((x) => Object.fromEntries(keep.filter((k) => x[k] != null).map((k) => [k, x[k]])));
  const prompt =
    "A member of Overlay Tips (an Aussie sports tips service) asked why these picks are on our board. Explain each " +
    "one in 2-3 short sentences, casual Aussie punter voice: what the numbers say for it and the main risk against " +
    "it. Use ONLY this data, don't invent injuries, news or stats. 'l10' is his record over the last 15 games " +
    "[hit, games], 'l5' the last 5, 'vs' vs tonight's opponent, 'prob' our rated chance, 'mkt' the market's, " +
    "'value' expected return %, 'games' newest first [date, opp, home/away, stat, minutes]. No emojis, no " +
    "'guaranteed'. Start each with the bet in bold like **Points 25+ @ $1.90 (Sportsbet)**.\n\n" + JSON.stringify(data);
  const g = await fetch(GEMINI + (env.GEMINI_MODEL || "gemini-flash-latest") + ":generateContent", {
    method: "POST", headers: { "Content-Type": "application/json", "x-goog-api-key": env.GEMINI_API_KEY },
    body: JSON.stringify({ contents: [{ role: "user", parts: [{ text: prompt }] }], generationConfig: { maxOutputTokens: 700 } }),
  });
  if (!g.ok) throw new Error("gemini " + g.status);
  const res = await g.json();
  const text = (((res.candidates || [])[0] || {}).content || {}).parts?.map((p) => p.text || "").join("").trim();
  if (!text) throw new Error("empty");
  return `**🤖 Why ${names[0]}?**\n${text.slice(0, 1700)}\n${WHY_FOOT}`;
}

async function verify(req, body, pub) {   // Discord signs every request with Ed25519
  const sig = req.headers.get("X-Signature-Ed25519"), ts = req.headers.get("X-Signature-Timestamp");
  if (!sig || !ts || !pub) return false;
  const data = new TextEncoder().encode(ts + body);
  for (const alg of [{ name: "Ed25519" }, { name: "NODE-ED25519", namedCurve: "NODE-ED25519" }]) {
    try {
      const key = await crypto.subtle.importKey("raw", hex(pub), alg, false, ["verify"]);
      return await crypto.subtle.verify(alg.name, key, hex(sig), data);
    } catch (e) { /* older runtime: try the other name */ }
  }
  return false;
}
