"""Build the Previews section from Markdown posts.

Posts live in previews/posts/<slug>.md and start with front matter:

    ---
    title: NBA preview: Wed 11 Nov
    date: 2026-11-11
    summary: One or two sentences for the list page and link previews.
    ---
    Markdown body...

Supported Markdown: ## / ### headings, paragraphs, - bullet lists, | pipe | tables |,
**bold**, *italic*, [links](url) and > callouts.

Run:  python3 scripts/build_previews.py
Writes previews/<slug>.html, previews/index.html and the previews entries in sitemap.xml.
"""
import html, os, re
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS = os.path.join(ROOT, "previews", "posts")
OUT = os.path.join(ROOT, "previews")
SITE = "https://overlay.tips"

NAV = [("index.html", "Home", ""), ("odds.html", "Odds", "hot"), ("hot.html", "Hot stats", ""), ("live.html", "Live", ""), ("lineups.html", "Lineups", ""), ("tracker.html", "Bet tracker", ""),
       ("previews/", "Previews", ""), ("membership.html", "Membership", ""), ("index.html#results", "Results", ""),
       ("calculator.html", "Bonus bet calc", ""), ("giveaway.html", "Giveaway", ""), ("merch.html", "Merch", ""),
       ("about.html", "About", "")]

CSS = """
.post{max-width:720px}
.post h2{font-size:26px!important;margin:34px 0 10px!important}
.post h3{font-size:19px;margin:24px 0 8px;color:var(--mint2)}
.post p{color:#c9d6d0;font-size:17px;line-height:1.65;margin:0 0 14px}
.post ul{margin:0 0 16px 20px;color:#c9d6d0;font-size:17px;line-height:1.6}
.post li{margin-bottom:6px}
.post b,.post strong{color:#ecf5f1}
.post a{color:var(--mint)}
.post blockquote{border-left:3px solid var(--gold);background:#0e1312;border-radius:0 12px 12px 0;padding:12px 16px;margin:0 0 18px;color:#c9d6d0}
.post blockquote p{margin:0;font-size:15.5px}
.post .tw{overflow-x:auto;margin:0 0 18px;border:1px solid #1b2523;border-radius:12px}
.post table{width:100%;border-collapse:collapse;font-size:14.5px}
.post th,.post td{padding:9px 10px;border-bottom:1px solid #1b2523;text-align:left;vertical-align:top}
.post th{color:#87978f;font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;font-weight:700;background:#0e1312}
.post tr:last-child td{border-bottom:0}
.post-meta{color:#87978f;font-size:14px;margin-top:-4px}
.post-foot{margin-top:30px;padding-top:18px;border-top:1px solid #1b2523;color:#87978f;font-size:14px;line-height:1.55}
.pv-list{display:grid;gap:14px;max-width:760px}
.pv-item{display:block;background:linear-gradient(180deg,#101716,#0b100f);border:1px solid #1b2523;border-radius:16px;padding:16px 18px;color:inherit;text-decoration:none}
.pv-item:hover{border-color:var(--mint-dim,#1d5a3f)}
.pv-item b{display:block;color:#ecf5f1;font-size:19px;margin:2px 0 4px}
.pv-item span{color:#87978f;font-size:13px}
.pv-item p{color:#c9d6d0;font-size:15px;margin:0}
"""


def inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{html.escape(m.group(2))}"'
               + (' target="_blank" rel="noopener"' if m.group(2).startswith("http") else "") + f">{m.group(1)}</a>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    return s


def md(text):
    out, para, lst, tbl, quote = [], [], [], [], []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>"); para.clear()
        if lst:
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in lst) + "</ul>"); lst.clear()
        if quote:
            out.append("<blockquote><p>" + inline(" ".join(quote)) + "</p></blockquote>"); quote.clear()
        if tbl:
            rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in tbl
                    if not re.fullmatch(r"\|?[\s:|-]+\|?", r.strip())]
            head, body = rows[0], rows[1:]
            out.append('<div class="tw"><table><tr>' + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body)
                       + "</table></div>")
            tbl.clear()

    for line in text.splitlines():
        t = line.strip()
        if not t:
            flush(); continue
        if t.startswith("### "):
            flush(); out.append(f"<h3>{inline(t[4:])}</h3>")
        elif t.startswith("## "):
            flush(); out.append(f"<h2>{inline(t[3:])}</h2>")
        elif t.startswith("|"):
            if not tbl: flush()
            tbl.append(t)
        elif re.match(r"^[-*] ", t):
            if not lst: flush()
            lst.append(t[2:])
        elif t.startswith(">"):
            if not quote: flush()
            quote.append(t.lstrip("> "))
        elif lst and line.startswith("  "):
            lst[-1] += " " + t
        else:
            if lst or tbl or quote: flush()
            para.append(t)
    flush()
    return "\n".join(out)


def parse(path):
    raw = open(path, encoding="utf-8").read()
    m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
    if not m:
        raise SystemExit(f"{path}: missing front matter")
    meta = dict(l.split(":", 1) for l in m.group(1).splitlines() if ":" in l)
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    for k in ("title", "date", "summary"):
        if not meta.get(k):
            raise SystemExit(f"{path}: front matter needs {k}")
    meta["slug"] = os.path.splitext(os.path.basename(path))[0]
    meta["body"] = m.group(2)
    return meta


def nice_date(d):
    d = date.fromisoformat(d)
    return d.strftime("%a %-d %b %Y")


def page(title, desc, canon, body, active="previews/"):
    pre = "../"
    def link(href, label, cls, tabbar=False):
        on = href == active
        c = " ".join(x for x in (cls, "on" if on else "") if x)
        return (f'<a href="{pre}{href}"' + (f' class="{c}"' if c else "") + (' aria-current="page"' if on else "")
                + f">{label}</a>")
    links = "\n      ".join(link(*n) for n in NAV)
    e = html.escape
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="icon" type="image/png" sizes="48x48" href="{pre}favicon-48.png">
<link rel="icon" type="image/png" sizes="96x96" href="{pre}favicon-96.png">
<link rel="icon" type="image/png" sizes="192x192" href="{pre}favicon-192.png">
<link rel="apple-touch-icon" href="{pre}apple-touch-icon.png">
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{canon}">
<meta name="robots" content="index, follow">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Overlay Tips">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{canon}">
<meta property="og:image" content="{SITE}/og-image.jpg">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#060807">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:ital,wght@0,400;0,500;0,700;0,900;1,800;1,900&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{pre}site.css?v=20261111a">
<link rel="stylesheet" href="{pre}plain.css?v=6">
<style>{CSS}</style>
<script src="{pre}nav.js?v=1"></script>
</head>
<body>

<nav>
  <div class="wrap navrow">
    <a href="{pre}index.html" class="brand" aria-label="Overlay Tips home">
      <img src="{pre}logo-mark.png?v=3" alt="" class="brand-mark">
      <span class="wm-o">OVERLAY</span> <span class="wm-t">TIPS</span>
    </a>
    <div class="navlinks">
      {links}
      <a class="btn btn-mint" href="https://www.instagram.com/overlay_tips/" target="_blank" rel="noopener">Join free</a>
    </div>
  </div>
  <div class="tabbar" aria-label="Site sections">
    {links}
  </div>
</nav>

<main>
{body}
</main>

<footer>
  <div class="wrap">
    <div class="rg">
      <b>18+ only. Gamble responsibly.</b> Analysed tips, not guaranteed wins. Only bet what you can afford to lose.
      Need help? Call Gambling Help Online on <b>1800 858 858</b> or visit <a href="https://www.gamblinghelponline.org.au" target="_blank" rel="noopener">gamblinghelponline.org.au</a>.
      You can self-exclude from all Australian bookmakers at <a href="https://www.betstop.gov.au" target="_blank" rel="noopener">betstop.gov.au</a>.
    </div>
    <div class="foot">
      <span>© 2026 Overlay Tips · 1U = $10 in examples</span>
      <span>Past results don't guarantee future profit. · <a href="{pre}terms.html">Giveaway T&amp;Cs</a></span>
    </div>
  </div>
</footer>
</body>
</html>
"""


FOOT_NOTE = ("Prices were taken from the Overlay Tips odds tracker at the time of writing and move quickly, so check "
             "the price is still there before you bet. These are price and form signals, not sure things. "
             "Live prices: <a href=\"../odds.html\">Odds board</a> · <a href=\"../hot.html\">Hot stats</a>.")


def build():
    posts = sorted((parse(os.path.join(POSTS, f)) for f in os.listdir(POSTS) if f.endswith(".md")),
                   key=lambda p: (p["date"], p["slug"]), reverse=True)
    for p in posts:
        e = html.escape
        body = f"""<header class="page-head">
  <div class="wrap">
    <div class="mono">Preview · {e(nice_date(p['date']))}</div>
    <h1>{e(p['title'])}</h1>
    <p>{e(p['summary'])}</p>
  </div>
</header>
<section class="page-sec" style="padding-top:8px">
  <div class="wrap"><article class="post">
{md(p['body'])}
    <div class="post-foot">{FOOT_NOTE}<br><a href="./">← All previews</a></div>
  </article></div>
</section>"""
        with open(os.path.join(OUT, p["slug"] + ".html"), "w", encoding="utf-8") as f:
            f.write(page(f"{p['title']} | Overlay Tips", p["summary"], f"{SITE}/previews/{p['slug']}.html", body))

    items = "\n".join(f'<a class="pv-item" href="{p["slug"]}.html"><span>{html.escape(nice_date(p["date"]))}</span>'
                      f'<b>{html.escape(p["title"])}</b><p>{html.escape(p["summary"])}</p></a>' for p in posts)
    body = f"""<header class="page-head">
  <div class="wrap">
    <div class="mono">Free · Daily</div>
    <h1>Previews</h1>
    <p>Each morning's slate: the best prices, value picks and in-form players, using the Overlay Tips odds tracker.</p>
  </div>
</header>
<section class="page-sec" style="padding-top:8px">
  <div class="wrap"><div class="pv-list">
{items or '<p class="post-meta">First preview coming soon.</p>'}
  </div></div>
</section>"""
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(page("Previews | Daily NBA betting previews | Overlay Tips",
                     "Daily previews: best prices, value picks and in-form players for today's games.",
                     f"{SITE}/previews/", body))

    # sitemap: replace previews entries
    sm = os.path.join(ROOT, "sitemap.xml")
    s = open(sm, encoding="utf-8").read()
    s = re.sub(r"\s*<url><loc>https://overlay\.tips/previews/[^<]*</loc>.*?</url>", "", s)
    entries = [f'  <url><loc>{SITE}/previews/</loc><lastmod>{posts[0]["date"] if posts else date.today()}</lastmod><changefreq>daily</changefreq></url>']
    entries += [f'  <url><loc>{SITE}/previews/{p["slug"]}.html</loc><lastmod>{p["date"]}</lastmod></url>' for p in posts]
    s = s.replace("</urlset>", "\n".join(entries) + "\n</urlset>")
    open(sm, "w", encoding="utf-8").write(s)
    print(f"built {len(posts)} previews")


if __name__ == "__main__":
    build()
