// Overlay Tips: AI helpers for the website, so the Gemini key never reaches the browser.
//
// POST /slip   body {"image": "<base64 JPEG/PNG>", "mime": "image/jpeg"}   (from overlay.tips only)
// -> {"text": "..."}  Gemini's line-by-line transcription of a bet slip screenshot. The bet tracker (slip.js)
//    then runs its own parser over that text, the same way it does with the in-browser OCR, and falls back to
//    that OCR if this fails. Gemini only transcribes: it doesn't decide anything about the bet.
//
// Cloudflare setup: Workers & Pages -> Create -> Worker, name it "overlay-ai", paste this file, Deploy.
// Then Settings -> Variables and Secrets:
//   GEMINI_API_KEY  (Secret) your Google AI Studio key
//   GEMINI_MODEL    (Text, optional) default gemini-flash-latest
// The site expects it at https://overlay-ai.borgjayden005.workers.dev (see AI_URL in slip.js).

const ORIGINS = ["https://overlay.tips", "https://www.overlay.tips"];
const MAX_B64 = 2_800_000;   // ~2MB image; slip.js shrinks screenshots well under this
const PER_MIN = 6;           // slips per minute per IP (per Worker instance, a speed bump not a wall)
const hits = new Map();

export default {
  async fetch(req, env) {
    const origin = req.headers.get("Origin") || "";
    const cors = {
      "Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Vary": "Origin",
    };
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (!ORIGINS.includes(origin)) return json({ error: "forbidden" }, 403, cors);
    if (!env.GEMINI_API_KEY) return json({ error: "not set up" }, 500, cors);
    const url = new URL(req.url);
    if (req.method !== "POST" || url.pathname !== "/slip") return json({ error: "not found" }, 404, cors);

    const ip = req.headers.get("CF-Connecting-IP") || "?", now = Date.now();
    const recent = (hits.get(ip) || []).filter((t) => now - t < 60_000);
    if (recent.length >= PER_MIN) return json({ error: "slow down" }, 429, cors);
    recent.push(now); hits.set(ip, recent);
    if (hits.size > 5000) hits.clear();

    let body;
    try { body = await req.json(); } catch (e) { return json({ error: "bad body" }, 400, cors); }
    const img = String(body.image || ""), mime = /^image\/(png|jpeg|webp)$/.test(body.mime) ? body.mime : "image/jpeg";
    if (!img || img.length > MAX_B64 || !/^[A-Za-z0-9+/=]+$/.test(img.slice(0, 200))) return json({ error: "image" }, 400, cors);

    const prompt =
      "This is a screenshot from an Australian bookmaker app or site (a bet slip, bet receipt or My Bets list). " +
      "Transcribe the betting text line by line, top to bottom, exactly as shown: bookie name, sport/competition, " +
      "event (e.g. 'Lakers v Celtics'), every selection/leg (player, market, line like 'Over 24.5' or '25+'), odds, " +
      "stake, potential return, bonus bet labels, race number/venue/runner. Keep each value on the same line as its " +
      "label (e.g. 'Stake $10.00'). Skip phone status bars, menus, ads and promos. Don't add, guess or fix anything; " +
      "if part is unreadable leave it out. Plain text only, no commentary.";
    const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${env.GEMINI_MODEL || "gemini-flash-latest"}:generateContent`, {
      method: "POST", headers: { "Content-Type": "application/json", "x-goog-api-key": env.GEMINI_API_KEY },
      body: JSON.stringify({
        contents: [{ role: "user", parts: [{ text: prompt }, { inline_data: { mime_type: mime, data: img } }] }],
        generationConfig: { maxOutputTokens: 1500 },
      }),
    });
    if (!r.ok) return json({ error: "gemini " + r.status }, 502, cors);
    const res = await r.json();
    const text = ((((res.candidates || [])[0] || {}).content || {}).parts || []).map((p) => p.text || "").join("").trim();
    if (!text) return json({ error: "empty" }, 502, cors);
    return json({ text: text.replace(/^```[a-z]*\n?|```$/g, "").trim() }, 200, cors);
  },
};

function json(obj, status, headers) {
  return new Response(JSON.stringify(obj), { status, headers: { ...headers, "Content-Type": "application/json", "Cache-Control": "no-store" } });
}
