/* Overlay Tips members gate.
   Pages that include this script (with data-need="member") are shown only to people logged in to the
   members portal with an active account (Founding, Core/Daily card or VIP). Everyone else sees a
   "Sign up" card. It reads the Supabase login the portal saves in this browser and asks Supabase
   for the account's tier, refreshing the login if it has expired. */
(function () {
  var SB = 'https://enldqvdxygppxwrauqlw.supabase.co';
  var KEY = 'sb_publishable_Zznc-4Nv6Ibb_SyE55jn6A_IqSxnYW8';
  var STORE = 'sb-enldqvdxygppxwrauqlw-auth-token';
  var RANK = { pending: 0, founder: 1, core: 2, vip: 3 };
  var me = document.currentScript;
  var need = (me && me.getAttribute('data-need')) || 'member';
  var needRank = need === 'vip' ? 3 : need === 'core' ? 2 : 1;
  var label = me && me.getAttribute('data-label') || 'this page';
  var root = document.documentElement;
  var base = (me && me.getAttribute('src') || '').replace(/gate\.js.*$/, '');
  root.classList.add('og-locked');

  var css = [
    'html.og-locked main .page-sec{filter:blur(7px);opacity:.45;pointer-events:none;user-select:none;max-height:900px;overflow:hidden}',
    '.og-card{max-width:520px;width:calc(100% - 32px);box-sizing:border-box;margin:6px auto 30px;background:linear-gradient(160deg,#0f2a1e,#0b1210 60%);border:1px solid #1d5a3f;border-radius:20px;padding:24px 22px;text-align:center;font-family:Archivo,system-ui,sans-serif}',
    '.og-card h2{font-size:24px!important;margin:0 0 8px!important;color:#ecf5f1}',
    '.og-card p{color:#c9d6d0;font-size:15px;margin:0 0 16px;line-height:1.5}',
    '.og-card .og-btns{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}',
    '.og-card a{display:inline-block;padding:11px 20px;border-radius:999px;font-weight:800;font-size:15px;text-decoration:none}',
    '.og-card a.p{background:#62f0a8;color:#04140c}.og-card a.s{border:1px solid #1d5a3f;color:#a8f7cf}',
    '.og-card small{display:block;color:#87978f;font-size:12.5px;margin-top:12px}'
  ].join('\n');
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

  function readSession() {
    try { var t = JSON.parse(localStorage.getItem(STORE)); return t && (t.currentSession || t); } catch (e) { return null; }
  }
  function saveSession(s) { try { localStorage.setItem(STORE, JSON.stringify(s)); } catch (e) {} }

  function card(kind) {
    var old = document.getElementById('ogCard'); if (old) old.remove();
    var c = document.createElement('div'); c.className = 'og-card'; c.id = 'ogCard';
    var portal = base + 'portal.html';
    if (kind === 'pending') {
      c.innerHTML = '<h2>Your account isn\'t active yet</h2><p>Open the members portal to see what\'s next.</p><div class="og-btns"><a class="p" href="' + portal + '">Members portal →</a></div>';
    } else if (kind === 'upgrade') {
      c.innerHTML = '<h2>This is for ' + (needRank === 3 ? 'VIP' : 'Daily card') + ' members</h2><p>Upgrade to unlock ' + label + '.</p><div class="og-btns"><a class="p" href="' + base + 'membership.html">See memberships →</a></div>';
    } else {
      c.innerHTML = '<h2>Sign up to see ' + label + '</h2><p>Get access to Overlay Wire, Hot stats and the bet tracker. Takes 30 seconds.</p><div class="og-btns"><a class="p" href="' + portal + '?signup">Sign up</a><a class="s" href="' + portal + '">Log in</a></div><small>18+ only. Gamble responsibly.</small>';
    }
    var head = document.querySelector('main .page-head') || document.querySelector('main');
    if (head && head.parentNode) head.parentNode.insertBefore(c, head.nextSibling);
  }
  function unlock() { root.classList.remove('og-locked'); var c = document.getElementById('ogCard'); if (c) c.remove(); }

  function tier(token) {
    return fetch(SB + '/rest/v1/rpc/my_tier', {
      method: 'POST', headers: { apikey: KEY, Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' }, body: '{}'
    }).then(function (r) { if (r.status === 401) throw 'auth'; if (!r.ok) throw 'net'; return r.json(); });
  }
  function refresh(s) {
    return fetch(SB + '/auth/v1/token?grant_type=refresh_token', {
      method: 'POST', headers: { apikey: KEY, 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh_token: s.refresh_token })
    }).then(function (r) { if (!r.ok) throw 'auth'; return r.json(); })
      .then(function (n) { n.expires_at = n.expires_at || Math.floor(Date.now() / 1000) + (n.expires_in || 3600); saveSession(n); return n; });
  }
  function decide(t) {
    var r = RANK[t] || 0;
    if (r >= needRank) unlock(); else card(r === 0 ? 'pending' : 'upgrade');
  }

  function go() {
    var s = readSession();
    if (!s || !s.access_token) { card('signup'); return; }
    var expired = s.expires_at && s.expires_at * 1000 < Date.now() + 30000;
    var p = expired && s.refresh_token ? refresh(s).then(function (n) { return tier(n.access_token); }) : tier(s.access_token);
    p.then(decide).catch(function (e) {
      if (e === 'auth' && s.refresh_token && !expired) {
        refresh(s).then(function (n) { return tier(n.access_token); }).then(decide).catch(function () { card('signup'); });
      } else if (e === 'auth') { card('signup'); }
      else { unlock(); }   // Supabase unreachable: don't lock members out
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
})();
