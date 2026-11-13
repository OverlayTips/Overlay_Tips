/* Overlay Tips: read a bet slip screenshot into the bet tracker.
   Runs entirely in the browser: Tesseract OCR (self-hosted in /ocr), then parseSlip() turns the text
   into {kind, player, stat, side, line, legs, desc, odds, stake, book}. The tracker opens it in the
   add-bet form so the member checks it before saving.
   OTSlip.read(file, names, onProgress) -> Promise<{bet, text}>;  OTSlip.parse(text, names) -> bet */
(function (root) {
  var BOOKS = [['tabtouch', 'TABtouch'], ['sportsbet', 'Sportsbet'], ['ladbrokes', 'Ladbrokes'], ['pointsbet', 'PointsBet'],
    ['betright', 'BetRight'], ['picklebet', 'Picklebet'], ['unibet', 'Unibet'], ['dabble', 'Dabble'], ['bet365', 'bet365'],
    ['playup', 'PlayUp'], ['betr', 'Betr'], ['neds', 'Neds'], ['tab', 'TAB']];
  // longest phrases first; the value is the tracker's stat name
  var STATS = [
    [/p(?:oin)?ts?\s*\+\s*reb(?:ound)?s?\s*\+\s*a(?:ss(?:ist)?)?s?t?s?|points,?\s*rebounds\s*(?:&|and|\+)\s*assists|\bpra\b/, 'Pts + Reb + Ast'],
    [/p(?:oin)?ts?\s*\+\s*reb(?:ound)?s?|points\s*(?:&|and)\s*rebounds/, 'Pts + Reb'],
    [/p(?:oin)?ts?\s*\+\s*ast(?:s|ists)?|points\s*(?:&|and)\s*assists/, 'Pts + Ast'],
    [/reb(?:ound)?s?\s*\+\s*ast(?:s|ists)?|rebounds\s*(?:&|and)\s*assists/, 'Reb + Ast'],
    [/steals?\s*\+\s*blocks?|stocks/, 'Steals + Blocks'],
    [/three|3\s*-?\s*p(?:oin)?t|3pm|3s\b|\bthrees\b/, 'Threes'],
    [/rebound|\breb\b|\brebs\b/, 'Rebounds'],
    [/assist|\bast\b|\basts\b/, 'Assists'],
    [/steal/, 'Steals'],
    [/block/, 'Blocks'],
    [/turnover/, 'Turnovers'],
    [/point|\bpts\b/, 'Points']
  ];
  var NUM = '(\\d{1,3}(?:[.,]\\d)?)';

  function clean(t) {
    return String(t || '').replace(/\r/g, '').replace(/[|¦]/g, ' ').replace(/[’`]/g, "'")
      .replace(/(\d)\s*[,·]\s*(\d)/g, '$1.$2')     // "24,5" / OCR'd decimal points
      .replace(/[ \t]+/g, ' ');
  }
  function statOf(s) {
    s = s.toLowerCase();
    for (var i = 0; i < STATS.length; i++) if (STATS[i][0].test(s)) return STATS[i][1];
    return null;
  }
  function money(s) { var m = String(s).match(/\$?\s*(\d{1,6}(?:[.,]\d{2})?)/); return m ? parseFloat(m[1].replace(',', '.')) : NaN; }

  // ---------- player names: snap OCR'd names onto the known list (NBA + NBL) ----------
  function norm(n) {
    return String(n || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[.'’`]/g, '')
      .replace(/-/g, ' ').split(/\s+/).filter(function (t) { return t && ['jr', 'sr', 'ii', 'iii', 'iv'].indexOf(t) < 0; }).join(' ');
  }
  function lev(a, b) {
    if (a === b) return 0;
    var d = [], i, j;
    for (i = 0; i <= b.length; i++) d[i] = i;
    for (i = 1; i <= a.length; i++) {
      var prev = d[0]; d[0] = i;
      for (j = 1; j <= b.length; j++) { var t = d[j]; d[j] = Math.min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1)); prev = t; }
    }
    return d[b.length];
  }
  function snapName(raw, names) {
    var n = norm(raw);
    if (!n || !names || !names.length) return raw.trim();
    var best = null, bd = 99;
    for (var i = 0; i < names.length; i++) {
      var k = norm(names[i]); if (!k) continue;
      var dd = lev(n, k);
      if (dd < bd) { bd = dd; best = names[i]; }
      // surname-only slips ("Jokic 25+ Points") -> unique surname match
      if (n.indexOf(' ') < 0 && k.split(' ').pop() === n) { var hits = names.filter(function (x) { return norm(x).split(' ').pop() === n; }); if (hits.length === 1) return hits[0]; }
    }
    return bd <= Math.max(2, Math.floor(n.length / 5)) ? best : raw.trim();
  }
  var NAME_RE = /([A-Z][A-Za-z'’.\-]+(?:\s+(?:[A-Z][A-Za-z'’.\-]+|de|da|van|la|le)){0,3})/;
  var NOT_NAME = /^(over|under|to|player|points?|rebounds?|assists?|threes?|steals?|blocks?|same|game|multi|single|stake|bet|return|odds|price|est|potential|payout|win|cash|total|my|bets?|pending|open|placed|nba|nbl|basketball|market|markets|alternate|alt|lines?|made|record|scorer|any|both|pts|reb|rebs|ast|asts|pra|stl|blk|stocks|threes?|3pm|win|odds|cash|out|est|head|to|line|h2h)$/i;
  function nameIn(s) {
    var words = s.replace(/[^A-Za-z'’.\- ]/g, ' ').split(/\s+/).filter(Boolean), out = [];
    for (var i = 0; i < words.length; i++) {
      var w = words[i];
      if (/^[A-Z]/.test(w) && (w.length > 1 || out.length) && !NOT_NAME.test(w.replace(/[.']/g, ''))) out.push(w);
      else if (out.length >= 2) break;
      else if (out.length && !/^(de|da|van|la|le)$/.test(w)) out = [];
    }
    return out.length >= 1 ? out.slice(0, 4).join(' ') : '';
  }

  // ---------- one selection line -> {player, stat, side, line} ----------
  function selection(line, prev, next) {
    var s = line, low = s.toLowerCase(), m, side, val, stat = statOf(low) || statOf(prev || '') || statOf(next || '');
    if ((m = low.match(new RegExp('\\b(over|under|o|u)\\s*' + NUM)))) {
      side = /^u/.test(m[1]) ? 'Under' : 'Over'; val = parseFloat(m[2].replace(',', '.'));
    } else if ((m = low.match(new RegExp(NUM + '\\s*\\+')))) {
      side = 'Over'; val = parseFloat(m[1]) - 0.5;              // "25+ Points" = over 24.5
    } else if ((m = low.match(new RegExp('(?:to (?:score|get|record|have|make)|at least)\\s*' + NUM)))) {
      side = 'Over'; val = parseFloat(m[1]) - 0.5;
    } else return null;
    if (!stat || !(val >= 0) || val > 99) return null;
    var who = nameIn(s.split(/\b(?:over|under|to score|to get|to record|to have|to make|at least)\b|\b[ou]\s*(?=\d)|\d/i)[0]);
    if (!who) who = nameIn(prev || '');
    if (!who) return null;
    return { player: who, stat: stat, side: side, line: val };
  }

  function parse(text, names) {
    var t = clean(text), lines = t.split('\n').map(function (x) { return x.trim(); }).filter(Boolean), more = 0;
    // a "My Bets" list with several bets: keep the first one (each ends at its stake/return lines)
    var ends = [];
    lines.forEach(function (l, i) { if (/stake|wager|bet amount/i.test(l)) ends.push(i); });
    if (ends.length > 1) {
      var cut = ends[0] + 1;
      while (cut < lines.length && /return|payout|to win|winnings/i.test(lines[cut])) cut++;
      more = ends.length - 1; lines = lines.slice(0, cut); t = lines.join('\n');
    }
    var low = t.toLowerCase();
    var bet = { kind: 'prop' };
    if (/bonus bet|bonus cash|free bet/i.test(t)) bet.bonus = true;
    if (more) bet.more = more;
    for (var i = 0; i < BOOKS.length; i++) {
      if (new RegExp('\\b' + BOOKS[i][0] + '\\b').test(low.replace(/sportsbet\.com\.au/, 'sportsbet'))) { bet.book = BOOKS[i][1]; break; }
    }
    // stake, return, odds
    var m;
    if ((m = t.match(/(?:stake|bet amount|wager|you bet|amount)\s*:?\s*\$?\s*(\d{1,6}(?:\.\d{1,2})?)/i))) bet.stake = parseFloat(m[1]);
    else if ((m = t.match(/\$\s*(\d{1,5}(?:\.\d{2})?)\s*(?:stake|bet)\b/i))) bet.stake = parseFloat(m[1]);
    var ret = NaN;
    if ((m = t.match(/(?:est(?:imated)?\.?\s*return|potential (?:return|payout|winnings)|to return|returns?|payout|to (?:win|pay))\s*:?\s*\$?\s*(\d{1,7}(?:\.\d{1,2})?)/i))) ret = parseFloat(m[1]);
    var odds = NaN;
    if ((m = t.match(/(?:odds|price|@|©)\s*:?\s*\$?\s*(\d{1,4}\.\d{1,2})/i))) odds = parseFloat(m[1]);
    // selections
    var sels = [], seen = {};
    for (var j = 0; j < lines.length; j++) {
      var s = selection(lines[j], lines[j - 1], lines[j + 1]);
      if (!s) continue;
      s.player = snapName(s.player, names);
      var key = norm(s.player) + '|' + s.stat + '|' + s.side + '|' + s.line;
      if (seen[key]) continue; seen[key] = 1; sels.push(s);
    }
    if (!(odds > 1)) {   // a lone decimal price on the slip (not a line, stake or return)
      var priceText = lines.filter(function (l) { return !/stake|return|payout|cash ?out|bonus|balance|to win|winnings|bet \$/i.test(l); }).join('\n');
      var cands = (priceText.match(/(?:^|[\s$@])(\d{1,3}\.\d{2})(?![\d%])/g) || []).map(function (x) { return parseFloat(x.replace(/[^\d.]/g, '')); })
        .filter(function (v) { return v > 1 && v < 501 && v !== bet.stake && v !== ret && !sels.some(function (s) { return s.line === v; }); });
      if (cands.length) odds = cands[0];
    }
    if (!(odds > 1) && bet.stake > 0 && ret > bet.stake) odds = Math.round(ret / bet.stake * 100) / 100;
    if (odds > 1) bet.odds = odds;
    if (!(bet.stake > 0) && odds > 1 && ret > 0) bet.stake = Math.round(ret / odds * 100) / 100;

    if (sels.length >= 2) { bet.kind = 'sgm'; bet.legs = sels; }
    else if (sels.length === 1) Object.assign(bet, sels[0]);
    else {
      bet.kind = 'other';
      var JUNK = /stake|return|payout|odds|my bets|pending|placed|bet ?slip|cash ?out|^single$|^multi$|^singles?\b|basketball|^nba$|^nbl$|^win @|^\$?\d/i;
      var useful = lines.filter(function (l) { return /[A-Za-z]{3}/.test(l) && !JUNK.test(l); });
      var ev = useful.find(function (l) { return /\s(v|vs|@)\s/.test(l); }) || '';
      var picks = useful.filter(function (l) { return l !== ev && !/\s(v|vs|@)\s/.test(l); });
      var mk = picks.find(function (l) { return /head to head|h2h|line|handicap|total|to win|margin|winner|moneyline/i.test(l); });
      bet.desc = ((picks[0] || '') + (mk && mk !== picks[0] ? ' ' + mk : '')).trim().slice(0, 80);
      if (ev) bet.event = ev.slice(0, 80);
    }
    return bet;
  }

  // ---------- OCR ----------
  var BASE = (function () {
    try { var s = document.currentScript && document.currentScript.src; return s ? s.replace(/[^/]*$/, '') + 'ocr/' : 'ocr/'; } catch (e) { return 'ocr/'; }
  })();
  function loadScript(src) {
    return new Promise(function (ok, bad) { var s = document.createElement('script'); s.src = src; s.onload = ok; s.onerror = function () { bad(new Error('could not load ' + src)); }; document.head.appendChild(s); });
  }
  // grayscale, upscale small images, flip dark-mode screenshots to dark-on-light (what Tesseract reads best)
  function prep(file) {
    return new Promise(function (ok, bad) {
      var img = new Image(), url = URL.createObjectURL(file);
      img.onload = function () {
        var scale = img.width < 1000 ? 2 : 1, c = document.createElement('canvas');
        c.width = img.width * scale; c.height = img.height * scale;
        var x = c.getContext('2d'); x.imageSmoothingEnabled = true; x.drawImage(img, 0, 0, c.width, c.height);
        var d = x.getImageData(0, 0, c.width, c.height), p = d.data, sum = 0, n = p.length / 4;
        for (var i = 0; i < p.length; i += 4) { var g = 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2]; p[i] = p[i + 1] = p[i + 2] = g; sum += g; }
        if (sum / n < 128) for (var k = 0; k < p.length; k += 4) { p[k] = p[k + 1] = p[k + 2] = 255 - p[k]; }
        x.putImageData(d, 0, 0); URL.revokeObjectURL(url); ok(c);
      };
      img.onerror = function () { bad(new Error("that file isn't an image")); };
      img.src = url;
    });
  }
  var worker = null;
  function timeout(p, ms, msg) {
    return Promise.race([p, new Promise(function (_, bad) { setTimeout(function () { bad(new Error(msg)); }, ms); })]);
  }
  function read(file, names, onProgress) {
    var prog = onProgress || function () {};
    return (window.Tesseract ? Promise.resolve() : loadScript(BASE + 'tesseract.min.js')).then(function () {
      if (worker) return worker;
      prog('Loading the reader (first time only)…');
      worker = timeout(Tesseract.createWorker('eng', 1, {
        workerPath: BASE + 'worker.min.js', corePath: BASE, langPath: BASE, gzip: false,
        logger: function (m) { if (m.status === 'recognizing text') prog('Reading the slip… ' + Math.round(m.progress * 100) + '%'); }
      }), 90000, 'the reader took too long to load, check your connection and try again').catch(function (e) { worker = null; throw e; });
      return worker;
    }).then(function (w) {
      return prep(file).then(function (canvas) { prog('Reading the slip…'); return timeout(w.recognize(canvas), 60000, 'reading took too long, try a smaller screenshot'); });
    }).then(function (r) {
      var text = r.data.text || '';
      var bets = parseAll(text, names);
      return { bet: bets[0], bets: bets, text: text };
    });
  }

  // every bet on the screenshot: blocks end at each stake line (plus the return/payout lines after it)
  function parseAll(text, names) {
    var lines = clean(text).split('\n').map(function (x) { return x.trim(); }).filter(Boolean), blocks = [], cur = [];
    for (var i = 0; i < lines.length; i++) {
      cur.push(lines[i]);
      if (/stake|wager|bet amount/i.test(lines[i])) {
        while (i + 1 < lines.length && /return|payout|to win|winnings/i.test(lines[i + 1])) cur.push(lines[++i]);
        blocks.push(cur); cur = [];
      }
    }
    if (cur.length && (!blocks.length || cur.some(function (l) { return selectionLike(l); }))) blocks.push(cur);
    var bets = blocks.map(function (b) { return parse(b.join('\n'), names); })
      .filter(function (b) { return b.kind !== 'other' || b.odds > 1 || b.stake > 0; });
    return bets.length ? bets : [parse(text, names)];
  }
  function selectionLike(l) { return /\b(over|under)\s*\d|\d\s*\+/i.test(l); }

  var api = { parse: parse, parseAll: parseAll, read: read, snapName: snapName };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.OTSlip = api;
})(typeof window !== 'undefined' ? window : this);
