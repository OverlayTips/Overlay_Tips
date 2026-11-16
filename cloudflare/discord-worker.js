// Overlay Tips: the /stats command in the Overlay Discord ("/stats Zion" -> his live NBA line).
//
// Runs as a Cloudflare Worker that Discord calls (Developer Portal -> your app -> General Information ->
// Interactions Endpoint URL = this Worker's URL). Replies are only shown to the member who asked
// (ephemeral), and only members with a Founder / Daily Card / VIP role can use it, so BALLDONTLIE's data
// isn't re-served as a public feed.
//
// Cloudflare settings (Worker -> Settings -> Variables and Secrets):
//   BDL_KEY             (Secret) your BALLDONTLIE API key, same as the overlay-live Worker
//   DISCORD_PUBLIC_KEY  (Secret) Developer Portal -> General Information -> Public Key
//   ALLOWED_ROLE_IDS    (Text)   comma-separated role ids allowed to use /stats (the Discord setup
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

    if (it.type === 2 && it.data && it.data.name === "stats") {
      const allowed = String(env.ALLOWED_ROLE_IDS || "").split(",").map((s) => s.trim()).filter(Boolean);
      const roles = (it.member && it.member.roles) || [];
      if (allowed.length && !roles.some((r) => allowed.includes(r))) {
        return reply({ type: 4, data: { flags: 64, content:
          "🔒 /stats is for Overlay members. Sign up free at <https://overlay.tips/portal.html> and tap **Connect Discord**." } });
      }
      const q = String(((it.data.options || []).find((o) => o.name === "player") || {}).value || "").trim();
      ctx.waitUntil(answer(it, q, env));
      return reply({ type: 5, data: { flags: 64 } });   // "thinking…", then answer() edits it
    }
    return reply({ type: 4, data: { flags: 64, content: "Unknown command." } });
  },
};

async function answer(it, q, env) {
  let content;
  try {
    content = await statsFor(q, env);
  } catch (e) {
    content = "Couldn't get live stats right now. Try again in a minute.";
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
