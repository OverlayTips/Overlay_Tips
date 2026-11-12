/* Overlay Tips member keys.
   For members logged in to the portal, fetches the passwords their tier unlocks (live tracking for
   Daily card and VIP, the odds tracker for VIP) from Supabase, so they never have to type one.
   Supabase only returns a key to accounts whose tier allows it (row level security on member_keys).
   OTKeys.get('live') / OTKeys.get('odds') -> Promise<string|null>; OTKeys.loggedIn() -> boolean */
(function () {
  var SB = 'https://enldqvdxygppxwrauqlw.supabase.co';
  var KEY = 'sb_publishable_Zznc-4Nv6Ibb_SyE55jn6A_IqSxnYW8';
  var STORE = 'sb-enldqvdxygppxwrauqlw-auth-token';
  var cache = null;

  function read() { try { var t = JSON.parse(localStorage.getItem(STORE)); return t && (t.currentSession || t); } catch (e) { return null; } }
  function save(s) { try { localStorage.setItem(STORE, JSON.stringify(s)); } catch (e) {} }
  function refresh(s) {
    return fetch(SB + '/auth/v1/token?grant_type=refresh_token', {
      method: 'POST', headers: { apikey: KEY, 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh_token: s.refresh_token })
    }).then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (n) { n.expires_at = n.expires_at || Math.floor(Date.now() / 1000) + (n.expires_in || 3600); save(n); return n; });
  }
  function load(token) {
    return fetch(SB + '/rest/v1/member_keys?select=name,value', { headers: { apikey: KEY, Authorization: 'Bearer ' + token } })
      .then(function (r) { if (r.status === 401) throw 'auth'; if (!r.ok) throw 'net'; return r.json(); });
  }
  function all() {
    if (cache) return cache;
    var s = read();
    if (!s || !s.access_token) return Promise.resolve({});
    var expired = s.expires_at && s.expires_at * 1000 < Date.now() + 30000;
    var p = (expired && s.refresh_token ? refresh(s) : Promise.resolve(s))
      .then(function (x) { return load(x.access_token).catch(function (e) { if (e === 'auth' && s.refresh_token) return refresh(s).then(function (n) { return load(n.access_token); }); throw e; }); })
      .then(function (rows) { var o = {}; (rows || []).forEach(function (r) { o[r.name] = r.value; }); return o; })
      .catch(function () { cache = null; return {}; });
    cache = p; return p;
  }
  // why isn't it unlocking? -> {loggedIn, email, tier, keys, err}
  function info() {
    var s = read(); if (!s || !s.access_token) return Promise.resolve({ loggedIn: false });
    var email = s.user && s.user.email;
    var expired = s.expires_at && s.expires_at * 1000 < Date.now() + 30000;
    return (expired && s.refresh_token ? refresh(s) : Promise.resolve(s)).then(function (x) {
      var h = { apikey: KEY, Authorization: 'Bearer ' + x.access_token, 'Content-Type': 'application/json' };
      return Promise.all([
        fetch(SB + '/rest/v1/rpc/my_tier', { method: 'POST', headers: h, body: '{}' }).then(function (r) { return r.ok ? r.json() : 'error ' + r.status; }),
        fetch(SB + '/rest/v1/member_keys?select=name', { headers: h }).then(function (r) { return r.ok ? r.json().then(function (j) { return j.map(function (k) { return k.name; }); }) : r.text().then(function (t) { return 'error ' + r.status + ' ' + t.slice(0, 120); }); })
      ]).then(function (a) { return { loggedIn: true, email: email, tier: a[0], keys: a[1] }; });
    }).catch(function (e) { return { loggedIn: true, email: email, err: 'login expired, log in again' }; });
  }
  window.OTKeys = {
    info: info,
    get: function (name) { return all().then(function (o) { return o[name] || null; }); },
    loggedIn: function () { var s = read(); return !!(s && s.access_token); },
    reset: function () { cache = null; }
  };
})();
