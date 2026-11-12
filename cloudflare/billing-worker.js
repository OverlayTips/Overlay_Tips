// Overlay Tips: paid memberships with Stripe (Daily card = "core", VIP = "vip").
//
// Runs as a Cloudflare Worker named "overlay-billing" so the Stripe and Supabase secret keys never reach the browser.
// The portal (overlay.tips/portal.html) calls it; Stripe calls /webhook when someone pays, renews or cancels,
// and the worker switches their tier in Supabase.
//
// Cloudflare settings (Worker -> Settings -> Variables and Secrets):
//   STRIPE_SECRET_KEY      Secret  Stripe -> Developers -> API keys -> Secret key (sk_test_... while testing)
//   STRIPE_WEBHOOK_SECRET  Secret  Stripe -> Developers -> Webhooks -> your endpoint -> Signing secret (whsec_...)
//   SUPABASE_SERVICE_KEY   Secret  Supabase -> Project Settings -> API keys -> secret / service_role key
//   SUPABASE_URL           Text    https://enldqvdxygppxwrauqlw.supabase.co
//   PRICE_CORE_MONTH       Text    price_... (Daily card, monthly)
//   PRICE_CORE_WEEK        Text    price_... (Daily card, weekly)
//   PRICE_VIP_MONTH        Text    price_... (VIP, monthly)
//   PRICE_VIP_WEEK         Text    price_... (VIP, weekly)
//
// Stripe webhook endpoint: https://overlay-billing.<you>.workers.dev/webhook
//   events: checkout.session.completed, customer.subscription.created,
//           customer.subscription.updated, customer.subscription.deleted
//
// GET  /check                      -> which settings are set / missing (no values shown)
// GET  /plans                      -> {"plans": {"core-month": {"amount", "currency", "interval"}, ...}}
// POST /checkout {"plan"}          header Authorization: Bearer <Supabase access token>  -> {"url"} (Stripe Checkout)
// POST /manage                     header Authorization: Bearer <Supabase access token>  -> {"url"} (Stripe customer portal)
// POST /webhook                    Stripe only (signature checked)
//
// Referrals: a mate who signs up with a code gets 50% off their first month (coupon OVERLAY-REF50, made automatically).
// When they first pay, the member who referred them gets a week free: pushed onto their subscription if they're paying,
// otherwise saved as free_weeks and given as a free trial when they subscribe.

const ORIGINS = ["https://overlay.tips", "https://www.overlay.tips"];
const SITE = "https://overlay.tips";
const PLANS = { "core-month": "PRICE_CORE_MONTH", "core-week": "PRICE_CORE_WEEK", "vip-month": "PRICE_VIP_MONTH", "vip-week": "PRICE_VIP_WEEK" };
const LIVE = ["active", "trialing", "past_due"];          // statuses that keep a paid tier switched on
const REF_COUPON = "OVERLAY-REF50";
const FIELDS = "id,email,tier,is_admin,base_tier,ref_code,referred_by,referral_rewarded,free_weeks,stripe_customer_id,stripe_sub_id,sub_status";

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    if (url.pathname === "/webhook") return req.method === "POST" ? webhook(req, env) : new Response("ok");

    const origin = req.headers.get("Origin") || "";
    const cors = {
      "Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
      "Access-Control-Allow-Headers": "Authorization, Content-Type",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Vary": "Origin",
    };
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });

    // public: prices are shown on the website anyway; /check says which settings are missing (never their values)
    if (url.pathname === "/check") return json(check(env), 200, { "Content-Type": "application/json" });
    if (url.pathname === "/plans" && req.method === "GET") {
      try { return json({ plans: await plans(env) }, 200, { ...cors, "Access-Control-Allow-Origin": "*", "Cache-Control": "max-age=300" }); }
      catch (e) { return json({ error: String(e && e.message || e).slice(0, 300) }, 500, { ...cors, "Access-Control-Allow-Origin": "*" }); }
    }

    if (!ORIGINS.includes(origin)) return json({ error: "forbidden" }, 403, cors);
    if (!env.STRIPE_SECRET_KEY || !env.SUPABASE_SERVICE_KEY || !env.SUPABASE_URL) return json({ error: "not set up" }, 500, cors);

    try {
      if (req.method !== "POST") return json({ error: "not found" }, 404, cors);
      const user = await whoami(req, env);
      if (!user) return json({ error: "Log in first." }, 401, cors);
      const prof = (await sbGet(env, `profiles?id=eq.${user.id}&select=${FIELDS}`))[0];
      if (!prof) return json({ error: "Account not found." }, 404, cors);
      if (url.pathname === "/checkout") return json(await checkout(env, prof, await req.json().catch(() => ({}))), 200, cors);
      if (url.pathname === "/manage") return json(await manage(env, prof), 200, cors);
      return json({ error: "not found" }, 404, cors);
    } catch (e) {
      return json({ error: String(e && e.message || e).slice(0, 300) }, 400, cors);
    }
  },
};

/* ---------- portal endpoints ---------- */

function check(env) {
  const keys = ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "SUPABASE_SERVICE_KEY", "SUPABASE_URL", ...Object.values(PLANS)];
  const out = Object.fromEntries(keys.map((k) => [k, env[k] ? "set" : "MISSING"]));
  out.stripe_mode = !env.STRIPE_SECRET_KEY ? "-" : env.STRIPE_SECRET_KEY.startsWith("sk_test_") ? "test" : env.STRIPE_SECRET_KEY.startsWith("sk_live_") ? "live" : "doesn't look like a secret key (should start sk_test_)";
  Object.values(PLANS).forEach((k) => { if (env[k] && !env[k].trim().startsWith("price_")) out[k] = "should start with price_ (looks like a product ID or typo)"; });
  return out;
}

async function plans(env) {
  if (!env.STRIPE_SECRET_KEY) throw new Error("STRIPE_SECRET_KEY is missing");
  const out = {};
  await Promise.all(Object.entries(PLANS).map(async ([plan, key]) => {
    if (!env[key]) return;
    const p = await stripe(env, "GET", `prices/${env[key].trim()}`);
    out[plan] = { amount: p.unit_amount / 100, currency: p.currency, interval: p.recurring && p.recurring.interval };
  }));
  return out;
}

async function checkout(env, prof, body) {
  const price = PLANS[body.plan] && (env[PLANS[body.plan]] || "").trim();
  if (!price) throw new Error("Unknown plan.");
  if (prof.is_admin) throw new Error("Admins already have everything.");
  if (prof.stripe_sub_id && LIVE.includes(prof.sub_status)) throw new Error("You already have a plan. Use Manage plan to switch or cancel.");

  const customer = await customerFor(env, prof);
  const params = {
    mode: "subscription",
    customer,
    client_reference_id: prof.id,
    "line_items[0][price]": price,
    "line_items[0][quantity]": 1,
    "subscription_data[metadata][user_id]": prof.id,
    "metadata[user_id]": prof.id,
    success_url: `${SITE}/portal.html?paid=1`,
    cancel_url: `${SITE}/portal.html?plans=1`,
  };
  // first-time payer who signed up with a mate's code: 50% off their first month
  if (prof.referred_by && !prof.referral_rewarded) {
    await refCoupon(env);
    params["discounts[0][coupon]"] = REF_COUPON;
  }
  // free weeks earned from referrals (while not paying) become a free trial
  if (prof.free_weeks > 0) {
    params["subscription_data[trial_period_days]"] = Math.min(prof.free_weeks * 7, 730);
    params["subscription_data[metadata][free_weeks_used]"] = prof.free_weeks;
  }
  const s = await stripe(env, "POST", "checkout/sessions", params);
  return { url: s.url };
}

async function manage(env, prof) {
  if (!prof.stripe_customer_id) throw new Error("You don't have a paid plan yet.");
  const s = await stripe(env, "POST", "billing_portal/sessions", { customer: prof.stripe_customer_id, return_url: `${SITE}/portal.html` });
  return { url: s.url };
}

async function customerFor(env, prof) {
  if (prof.stripe_customer_id) return prof.stripe_customer_id;
  const c = await stripe(env, "POST", "customers", { email: prof.email, "metadata[user_id]": prof.id });
  await sbPatch(env, `profiles?id=eq.${prof.id}`, { stripe_customer_id: c.id });
  return c.id;
}

async function refCoupon(env) {
  const r = await fetch(`https://api.stripe.com/v1/coupons/${REF_COUPON}`, { headers: { Authorization: `Bearer ${env.STRIPE_SECRET_KEY}` } });
  if (r.ok) return;
  await stripe(env, "POST", "coupons", { id: REF_COUPON, name: "Mate referral: 50% off first month", percent_off: 50, duration: "repeating", duration_in_months: 1 });
}

/* ---------- Stripe webhook ---------- */

async function webhook(req, env) {
  const body = await req.text();
  if (!(await validSignature(body, req.headers.get("Stripe-Signature") || "", env.STRIPE_WEBHOOK_SECRET || ""))) return new Response("bad signature", { status: 400 });
  const event = JSON.parse(body);
  const o = event.data.object;
  try {
    if (event.type === "checkout.session.completed" && o.mode === "subscription") {
      const id = o.client_reference_id || (o.metadata && o.metadata.user_id);
      if (id) await sbPatch(env, `profiles?id=eq.${id}`, { stripe_customer_id: o.customer });
      if (o.subscription) await syncSubscription(env, await stripe(env, "GET", `subscriptions/${o.subscription}`));
    } else if (event.type.startsWith("customer.subscription.")) {
      await syncSubscription(env, o);
    }
  } catch (e) {
    return new Response("error: " + String(e && e.message || e).slice(0, 300), { status: 500 });   // Stripe retries
  }
  return new Response("ok");
}

async function syncSubscription(env, sub) {
  let prof = sub.metadata && sub.metadata.user_id
    ? (await sbGet(env, `profiles?id=eq.${sub.metadata.user_id}&select=${FIELDS}`))[0]
    : (await sbGet(env, `profiles?stripe_customer_id=eq.${sub.customer}&select=${FIELDS}`))[0];
  if (!prof) return;
  // an old subscription ending must not undo a newer one
  if (prof.stripe_sub_id && prof.stripe_sub_id !== sub.id && LIVE.includes(prof.sub_status) && !LIVE.includes(sub.status)) return;

  const priceId = sub.items && sub.items.data[0] && sub.items.data[0].price.id;
  const plan = Object.keys(PLANS).find((p) => (env[PLANS[p]] || "").trim() === priceId) || null;
  const paidTier = plan ? plan.split("-")[0] : null;
  const end = sub.current_period_end || (sub.items && sub.items.data[0] && sub.items.data[0].current_period_end) || null;
  const patch = {
    stripe_customer_id: sub.customer,
    stripe_sub_id: sub.id,
    sub_status: sub.status,
    sub_plan: plan,
    paid_until: end ? new Date(end * 1000).toISOString() : null,
  };

  if (LIVE.includes(sub.status) && paidTier) {
    if (!["core", "vip"].includes(prof.tier)) patch.base_tier = prof.tier;   // remember founder/pending to fall back to
    patch.tier = paidTier;
    if (sub.metadata && +sub.metadata.free_weeks_used > 0 && prof.free_weeks > 0)
      patch.free_weeks = Math.max(0, prof.free_weeks - +sub.metadata.free_weeks_used);
    if (prof.referred_by && !prof.referral_rewarded) {
      patch.referral_rewarded = true;
      await rewardReferrer(env, prof.referred_by, prof.id);
    }
  } else if (["core", "vip"].includes(prof.tier)) {
    patch.tier = prof.base_tier || "pending";
  }
  if (prof.is_admin) delete patch.tier;
  await sbPatch(env, `profiles?id=eq.${prof.id}`, patch);
}

async function rewardReferrer(env, code, refereeId) {
  const r = (await sbGet(env, `profiles?ref_code=eq.${encodeURIComponent(code)}&select=${FIELDS}`))[0];
  if (!r || r.id === refereeId || r.is_admin) return;
  if (r.stripe_sub_id && LIVE.includes(r.sub_status)) {
    // push their next charge back a week (free week, no proration)
    const sub = await stripe(env, "GET", `subscriptions/${r.stripe_sub_id}`);
    const end = sub.current_period_end || (sub.items && sub.items.data[0] && sub.items.data[0].current_period_end);
    const from = Math.max(end || 0, sub.trial_end || 0, Math.floor(Date.now() / 1000));
    await stripe(env, "POST", `subscriptions/${sub.id}`, { trial_end: from + 7 * 86400, proration_behavior: "none" });
  } else {
    await sbPatch(env, `profiles?id=eq.${r.id}`, { free_weeks: (r.free_weeks || 0) + 1 });
  }
}

async function validSignature(body, header, secret) {
  if (!secret) return false;
  const v1 = [];
  let t = "";
  for (const kv of header.split(",")) {
    const [k, v] = kv.split("=");
    if (k === "t") t = v; else if (k === "v1") v1.push(v);
  }
  if (!t || !v1.length || Math.abs(Date.now() / 1000 - +t) > 300) return false;
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${t}.${body}`)));
  const hex = [...mac].map((b) => b.toString(16).padStart(2, "0")).join("");
  return v1.some((s) => s.length === hex.length && [...s].reduce((d, c, i) => d | (c.charCodeAt(0) ^ hex.charCodeAt(i)), 0) === 0);
}

/* ---------- helpers ---------- */

async function whoami(req, env) {
  const auth = req.headers.get("Authorization") || "";
  if (!auth.startsWith("Bearer ")) return null;
  const r = await fetch(`${env.SUPABASE_URL}/auth/v1/user`, { headers: { apikey: env.SUPABASE_SERVICE_KEY, Authorization: auth } });
  return r.ok ? r.json() : null;
}

async function sbGet(env, path) {
  const r = await fetch(`${env.SUPABASE_URL}/rest/v1/${path}`, { headers: sbHeaders(env) });
  if (!r.ok) throw new Error(`Supabase ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

async function sbPatch(env, path, data) {
  const r = await fetch(`${env.SUPABASE_URL}/rest/v1/${path}`, { method: "PATCH", headers: { ...sbHeaders(env), "Content-Type": "application/json", Prefer: "return=minimal" }, body: JSON.stringify(data) });
  if (!r.ok) throw new Error(`Supabase ${r.status}: ${(await r.text()).slice(0, 200)}`);
}

function sbHeaders(env) {
  return { apikey: env.SUPABASE_SERVICE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_KEY}` };
}

async function stripe(env, method, path, params) {
  const opts = { method, headers: { Authorization: `Bearer ${env.STRIPE_SECRET_KEY}` } };
  if (params) {
    opts.headers["Content-Type"] = "application/x-www-form-urlencoded";
    opts.body = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString();
  }
  const r = await fetch(`https://api.stripe.com/v1/${path}`, opts);
  const out = await r.json();
  if (!r.ok) throw new Error((out.error && out.error.message) || `Stripe ${r.status}`);
  return out;
}

function json(obj, status, headers) {
  return new Response(JSON.stringify(obj), { status, headers: { ...headers, "Content-Type": "application/json" } });
}
