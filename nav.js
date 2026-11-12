/* Overlay Tips sidebar menu.
   Builds a sidebar from the page's existing <nav> links: fixed on the left on desktop,
   a slide-out drawer (☰ button) on phones. Without JS the old top nav still works. */
(function () {
  var d = document, root = d.documentElement;
  root.classList.add('sbx');
  var css = [
    '.sbx-side{position:fixed;top:0;left:0;bottom:0;width:236px;z-index:45;background:#0a0e0d;border-right:1px solid #1b2523;display:flex;flex-direction:column;padding:20px 14px 18px;overflow-y:auto;font-family:Archivo,system-ui,sans-serif}',
    '.sbx-side .brand{display:flex;align-items:center;gap:9px;font-family:"Space Mono",monospace;font-weight:700;letter-spacing:.24em;font-size:13px;white-space:nowrap;padding:2px 6px 18px;text-decoration:none}',
    '.sbx-side .brand img{height:30px;width:auto}',
    '.sbx-side .brand img.brand-logo{height:64px}',
    '.sbx-side .brand .wm-o{color:#a8f7cf}.sbx-side .brand .wm-t{color:#efd38f}',
    '.sbx-links{display:flex;flex-direction:column;gap:3px}',
    '.sbx-links a{display:block;padding:10px 12px;border-radius:11px;color:#87978f;font-size:15px;font-weight:600;text-decoration:none;transition:background .15s,color .15s}',
    '.sbx-links a:hover{background:#121a18;color:#ecf5f1}',
    '.sbx-links a.hot:not(.on){color:#a8f7cf;box-shadow:inset 0 0 0 1px #1d5a3f}',
    '.sbx-links a.on{background:#a8f7cf;color:#04140c;font-weight:800;font-style:italic}',
    '.sbx-side .sbx-join{display:block;margin-top:16px;text-align:center;padding:11px 14px;border-radius:999px;background:#62f0a8;color:#04140c;font-weight:800;font-size:15px;text-decoration:none}',
    '.sbx-rg{margin-top:auto;padding:18px 6px 0;color:#5f6f68;font-size:12px;line-height:1.45}',
    '.sbx-x{display:none;position:absolute;top:14px;right:12px;width:38px;height:38px;border-radius:10px;border:1px solid #1b2523;background:#0e1312;color:#ecf5f1;font-size:22px;line-height:1;cursor:pointer}',
    '.sbx-menu{display:none;align-items:center;justify-content:center;width:42px;height:42px;margin-left:8px;border-radius:12px;border:1px solid #1b2523;background:#0e1312;color:#ecf5f1;cursor:pointer;flex:none}',
    '.sbx-menu svg{width:22px;height:22px}',
    '.sbx-dim{position:fixed;inset:0;z-index:44;background:rgba(0,0,0,.6);opacity:0;pointer-events:none;transition:opacity .2s}',
    'html.sbx .tabbar{display:none!important}',
    '@media(min-width:1024px){html.sbx body{padding-left:236px}html.sbx body>nav{display:none}}',
    '@media(max-width:1023px){',
    '.sbx-side{width:min(84vw,300px);transform:translateX(-105%);transition:transform .25s cubic-bezier(.2,.8,.2,1);box-shadow:0 0 40px rgba(0,0,0,.5)}',
    '.sbx-x{display:block}.sbx-menu{display:inline-flex}',
    'html.sbx nav .navlinks a:not(.btn){display:none!important}',
    'html.sbx-open .sbx-side{transform:none}html.sbx-open .sbx-dim{opacity:1;pointer-events:auto}html.sbx-open body{overflow:hidden}}'
    ,'.sbx-acct{display:inline-flex;align-items:center;gap:8px;height:42px;padding:0 14px 0 6px;margin-left:8px;border-radius:999px;border:1px solid #1b2523;background:#0e1312;color:#ecf5f1;font:700 14px Archivo,system-ui,sans-serif;text-decoration:none;white-space:nowrap;flex:none}'
    ,'.sbx-acct:hover{border-color:#1d5a3f}'
    ,'.sbx-acct .av{width:30px;height:30px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;background:#a8f7cf;color:#04140c;font-weight:900;font-size:13px}'
    ,'.sbx-acct.out{padding:0 16px}.sbx-acct.out .av{display:none}'
    ,'.sbx-acct .nm{max-width:110px;overflow:hidden;text-overflow:ellipsis}'
    ,'.sbx-acct-fix{display:none}'
    ,'html.sbx-member nav .navlinks a.btn{display:none!important}'
    ,'@media(max-width:380px){.sbx-acct.out{padding:0 10px;font-size:13px}nav .navlinks a.btn{padding-left:12px!important;padding-right:12px!important}}'
    ,'@media(min-width:1024px){.sbx-acct-fix{display:inline-flex;position:fixed;top:16px;right:22px;z-index:46;margin:0}}'
    ,'@media(max-width:1023px){html.sbx nav .navlinks a.sbx-acct{display:inline-flex!important}}'
    ,'@media(max-width:480px){.sbx-acct .nm{max-width:64px}.sbx-acct{padding:0 10px 0 5px;gap:6px}.sbx-acct.out{padding:0 12px}}'
    ,'.sbx-acct .fd{display:none;align-items:center;gap:4px;padding:3px 8px;border-radius:999px;background:linear-gradient(180deg,#ffe28a,#efbe3b);color:#1a1204;font:700 10.5px "Space Mono",monospace;letter-spacing:.08em;text-transform:uppercase}'
    ,'.sbx-acct.founder .fd{display:inline-flex}.sbx-acct.founder{border-color:#5a4a1d}.sbx-acct.founder .av{box-shadow:0 0 0 2px #efbe3b}'
    ,'@media(max-width:480px){.sbx-acct .fd .tx{display:none}.sbx-acct .fd{padding:3px 6px}}'
  ].join('\n');
  var st = d.createElement('style'); st.textContent = css; d.head.appendChild(st);

  function build() {
    var nav = d.querySelector('body > nav'), links = nav && nav.querySelector('.navlinks');
    if (!links) { root.classList.remove('sbx'); return; }
    var side = d.createElement('aside');
    side.className = 'sbx-side'; side.id = 'sbxSide'; side.setAttribute('aria-label', 'Site menu');
    var brand = nav.querySelector('.brand');
    if (brand) side.appendChild(brand.cloneNode(true));
    var list = d.createElement('div'); list.className = 'sbx-links';
    var join = null;
    Array.prototype.forEach.call(links.querySelectorAll('a'), function (a) {
      if (a.classList.contains('btn')) { join = a; return; }
      list.appendChild(a.cloneNode(true));
    });
    side.appendChild(list);
    if (join) { var j = join.cloneNode(true); j.className = 'sbx-join'; side.appendChild(j); }
    var rg = d.createElement('div'); rg.className = 'sbx-rg';
    rg.textContent = '18+ only. Gamble responsibly. Gambling Help 1800 858 858.';
    side.appendChild(rg);
    var x = d.createElement('button'); x.type = 'button'; x.className = 'sbx-x'; x.setAttribute('aria-label', 'Close menu'); x.textContent = '×';
    side.appendChild(x);
    var dim = d.createElement('div'); dim.className = 'sbx-dim';
    var btn = d.createElement('button');
    btn.type = 'button'; btn.className = 'sbx-menu'; btn.setAttribute('aria-label', 'Open menu');
    btn.setAttribute('aria-controls', 'sbxSide'); btn.setAttribute('aria-expanded', 'false');
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>';
    // account button (top right): your name when you're logged in to the members portal, otherwise "Log in"
    var home = (nav.querySelector('.brand') || {}).getAttribute ? nav.querySelector('.brand').getAttribute('href') || 'index.html' : 'index.html';
    var portal = home.replace(/index\.html.*$/, '') + 'portal.html';
    var who = null, tok = null, uid = null;
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (/^sb-.*-auth-token$/.test(k)) {
          var t = JSON.parse(localStorage.getItem(k)); t = t && (t.currentSession || t);
          var u = t && t.user;
          if (u && (!t.expires_at || t.expires_at * 1000 > Date.now() - 7 * 864e5)) {
            who = ((u.user_metadata && u.user_metadata.name) || (u.email || '').split('@')[0] || 'Member').trim();
            uid = u.id; if (t.access_token && (!t.expires_at || t.expires_at * 1000 > Date.now() + 30000)) tok = t.access_token;
          }
        }
      }
    } catch (e) {}
    function acct(cls) {
      var a = d.createElement('a'); a.href = portal; a.className = 'sbx-acct ' + cls + (who ? '' : ' out');
      if (who) {
        var first = who.split(/\s+/)[0];
        a.innerHTML = '<span class="av"></span><span class="nm"></span><span class="fd" title="Founding member">★<span class="tx">Founder</span></span>';
        a.querySelector('.av').textContent = first.charAt(0).toUpperCase();
        a.querySelector('.nm').textContent = first;
        a.title = 'Members portal: ' + who;
      } else { a.textContent = 'Log in'; a.title = 'Members portal'; }
      if (/portal\.html$/.test(location.pathname)) a.setAttribute('aria-current', 'page');
      return a;
    }
    if (who) root.classList.add('sbx-member');   // members don't need the "Join free" button
    var accts = [acct(''), acct('sbx-acct-fix')];
    links.appendChild(accts[0]);
    d.body.appendChild(accts[1]);
    // founder tag: gold "★ Founder" pill on the account button for founding members.
    // Shows the last known tier straight away, then checks it with Supabase.
    function tag(tier) { accts.forEach(function (a) { a.classList.toggle('founder', tier === 'founder'); }); }
    if (who && uid) {
      var TK = 'ot-tier-' + uid;
      try { tag(localStorage.getItem(TK)); } catch (e) {}
      if (tok) {
        fetch('https://enldqvdxygppxwrauqlw.supabase.co/rest/v1/rpc/my_tier', { method: 'POST', body: '{}',
          headers: { apikey: 'sb_publishable_Zznc-4Nv6Ibb_SyE55jn6A_IqSxnYW8', Authorization: 'Bearer ' + tok, 'Content-Type': 'application/json' } })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (t) { if (typeof t === 'string') { tag(t); try { localStorage.setItem(TK, t); } catch (e) {} } })
          .catch(function () {});
      }
    }
    links.appendChild(btn);
    d.body.appendChild(dim); d.body.appendChild(side);
    function set(open) {
      root.classList.toggle('sbx-open', open);
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) x.focus();
    }
    btn.onclick = function () { set(true); };
    x.onclick = dim.onclick = function () { set(false); btn.focus(); };
    list.addEventListener('click', function () { set(false); });
    d.addEventListener('keydown', function (e) { if (e.key === 'Escape' && root.classList.contains('sbx-open')) { set(false); btn.focus(); } });
  }
  if (d.readyState === 'loading') d.addEventListener('DOMContentLoaded', build); else build();
})();
