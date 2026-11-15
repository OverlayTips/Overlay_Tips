"""Weekly results graphic for Instagram (1080x1350), from the public Tracker record (odds-data/record.json).

Every Monday morning (see .github/workflows/weekly.yml) it draws last week's (Mon-Sun, Melbourne time) graded
odds-tracker alerts: record, units at the suggested stakes, flat-stake ROI, average CLV, the best winner and
the week's running profit, in the Overlay Tips colours. The PNG goes to the graphics branch and to Jayden's
ntfy channel with a ready-to-paste caption.

Usage: python scripts/weekly_graphic.py [record.json path or URL] [out.png] [--week-ending YYYY-MM-DD]
Env (optional): NTFY_TOPIC, GRAPHIC_URL (public URL of the PNG, for the ntfy attachment)
"""
import json, os, sys, urllib.request
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(ROOT, "vendor", "fonts")
MEL = ZoneInfo("Australia/Melbourne")
SRC = "https://raw.githubusercontent.com/OverlayTips/Overlay_Tips/odds-data/record.json"
W, H = 1080, 1350
BG, PANEL, LINE = (6, 8, 7), (14, 19, 18), (29, 42, 38)
MINT, MINT2, GOLD, RED, TEXT, MUTED = (98, 240, 168), (168, 247, 207), (239, 190, 59), (255, 107, 107), (236, 245, 241), (135, 151, 143)


def font(size, weight=800, width=100, italic=False, mono=False):
    if mono:
        return ImageFont.truetype(os.path.join(FONTS, "SpaceMono-Bold.ttf"), size)
    f = ImageFont.truetype(os.path.join(FONTS, "Archivo-Italic.ttf" if italic else "Archivo.ttf"), size)
    try:
        f.set_variation_by_axes([weight, width])
    except Exception:
        pass
    return f


def load(src):
    if src.startswith("http"):
        with urllib.request.urlopen(src, timeout=30) as r:
            return json.load(r)
    return json.load(open(src))


def week_of(rec, week_end):
    """Bets that started in the Mon-Sun week ending week_end (Melbourne dates)."""
    start = week_end - timedelta(days=6)
    pts = rec.get("units") or []   # [start iso, cumulative units, sel, result] one per graded bet
    bets, prev = [], 0.0
    for p in pts:
        d = datetime.fromisoformat(p[0].replace("Z", "+00:00")).astimezone(MEL).date()
        delta = round(p[1] - prev, 2)
        prev = p[1]
        if start <= d <= week_end and len(p) > 3 and p[3] in ("win", "loss"):
            bets.append({"d": d, "u": delta, "sel": p[2], "res": p[3], "t": p[0]})
    clv = [c[2] for c in rec.get("clv_days") or []
           if len(c) > 2 and start <= datetime.fromisoformat(c[0].replace("Z", "+00:00")).astimezone(MEL).date() <= week_end]
    prices = {}
    for b in rec.get("recent") or []:   # price of each bet, to call out the best winner
        prices[(b.get("sel"), str(b.get("start"))[:16])] = b
    return start, bets, clv, prices


def summary(bets, clv, prices):
    w = sum(1 for b in bets if b["res"] == "win")
    l = sum(1 for b in bets if b["res"] == "loss")
    units = round(sum(b["u"] for b in bets), 1)
    flat = 0.0
    best = None
    for b in bets:
        rb = prices.get((b["sel"], b["t"][:16]))
        price = rb and rb.get("price")
        stake = (rb and rb.get("stake")) or 1
        if b["res"] == "win":
            flat += (price - 1) if price else b["u"] / stake
            if price and (best is None or price > best["price"]):
                best = {"sel": b["sel"], "price": float(price), "game": rb.get("game", ""), "u": b["u"]}
            elif not price and (best is None or b["u"] > best.get("u", 0)) and not (best and best.get("price")):
                best = {"sel": b["sel"], "price": None, "game": "", "u": b["u"]}
        else:
            flat -= 1
    n = w + l
    return {"w": w, "l": l, "n": n, "units": units, "roi": round(100 * flat / n, 1) if n else None,
            "clv": round(sum(clv) / len(clv), 1) if clv else None, "best": best}


def draw(s, bets, start, end, out):
    im = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGB", (W, H), BG)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-200, -350, 1280, 620), fill=(16, 52, 37))
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    im = Image.blend(im, glow, 0.9)
    d = ImageDraw.Draw(im)

    logo = Image.open(os.path.join(ROOT, "logo-full.png")).convert("RGBA")
    lw = 300
    logo = logo.resize((lw, int(logo.height * lw / logo.width)), Image.LANCZOS)
    im.paste(logo, ((W - lw) // 2, 44), logo)
    y = 44 + logo.height + 12

    def center(txt, f, fill, yy):
        x = (W - d.textlength(txt, font=f)) / 2
        d.text((x, yy), txt, font=f, fill=fill)

    rng = f"{start.day} {start.strftime('%b')} – {end.day} {end.strftime('%b')}" if start.month != end.month else f"{start.day}–{end.day} {end.strftime('%b %Y')}"
    center("WEEK IN REVIEW", font(30, mono=True), MINT2, y)
    center(rng.upper(), font(26, mono=True), MUTED, y + 44)
    y += 92

    # headline: record + units
    up = s["units"] >= 0
    center(f"{s['w']}–{s['l']}", font(160, 900, 100, italic=True), GOLD, y - 10)
    y += 178
    center(f"{'+' if up else ''}{s['units']:g} UNITS", font(72, 900, 100, italic=True), MINT if up else RED, y)
    y += 104

    # tiles
    tiles = [("BETS", str(s["n"])), ("ROI", "–" if s["roi"] is None else f"{'+' if s['roi'] >= 0 else ''}{s['roi']:g}%"),
             ("AVG CLV", "–" if s["clv"] is None else f"{'+' if s['clv'] >= 0 else ''}{s['clv']:g}%")]
    tw, gap = 300, 30
    x0 = (W - (3 * tw + 2 * gap)) // 2
    for i, (k, v) in enumerate(tiles):
        x = x0 + i * (tw + gap)
        d.rounded_rectangle((x, y, x + tw, y + 136), 26, fill=PANEL, outline=LINE, width=2)
        kf, vf = font(24, mono=True), font(58, 900)
        d.text((x + (tw - d.textlength(k, font=kf)) / 2, y + 22), k, font=kf, fill=MUTED)
        d.text((x + (tw - d.textlength(v, font=vf)) / 2, y + 50), v, font=vf, fill=TEXT)
    y += 172

    # running profit for the week
    cx0, cx1, cy0, cy1 = 90, W - 90, y + 20, y + 190
    d.rounded_rectangle((cx0 - 30, cy0 - 20, cx1 + 30, cy1 + 30), 26, fill=PANEL, outline=LINE, width=2)
    run, c = [0.0], 0.0
    for b in bets:
        c += b["u"]
        run.append(c)
    lo, hi = min(run + [0]), max(run + [0])
    if hi - lo < 1:
        hi += 0.5; lo -= 0.5
    X = lambda i: cx0 + (cx1 - cx0) * i / max(1, len(run) - 1)
    Y = lambda v: cy0 + 20 + (cy1 - cy0 - 40) * (hi - v) / (hi - lo)
    d.line((cx0, Y(0), cx1, Y(0)), fill=(58, 71, 67), width=2)
    pts = [(X(i), Y(v)) for i, v in enumerate(run)]
    if len(pts) > 1:
        d.line(pts, fill=MINT if up else RED, width=6, joint="curve")
    for (px, py), b in zip(pts[1:], bets):
        r = 7 if len(bets) <= 60 else 4
        d.ellipse((px - r, py - r, px + r, py + r), fill=MINT if b["res"] == "win" else RED, outline=BG, width=2)
    d.text((cx0, cy0 - 12), "RUNNING PROFIT", font=font(22, mono=True), fill=MUTED)
    y = cy1 + 58

    # best winner
    b = s["best"]
    if b:
        d.rounded_rectangle((60, y, W - 60, y + 130), 26, fill=(15, 42, 30), outline=(29, 90, 63), width=2)
        d.text((95, y + 22), "BEST WINNER", font=font(22, mono=True), fill=MINT2)
        sel = b["sel"]
        f = font(46, 800)
        while d.textlength(sel, font=f) > W - 380 and len(sel) > 10:
            sel = sel[:-2]
        if sel != b["sel"]:
            sel = sel.rstrip() + "…"
        d.text((95, y + 56), sel, font=f, fill=TEXT)
        if b.get("price"):
            pf = font(70, 900, italic=True)
            pt = f"${b['price']:.2f}"
            d.text((W - 95 - d.textlength(pt, font=pf), y + 28), pt, font=pf, fill=GOLD)
        y += 170

    center("overlay.tips  ·  every alert tracked on the Tracker record", font(26, 600), MUTED, H - 118)
    center("18+ Gamble responsibly. Gambling Help 1800 858 858", font(22, mono=True), (95, 111, 104), H - 72)
    im.save(out, optimize=True)


def caption(s, start, end):
    b = s["best"]
    lines = [f"Week in review 📊 {start.day}–{end.day} {end.strftime('%b')}",
             f"{s['w']}–{s['l']} · {'+' if s['units'] >= 0 else ''}{s['units']:g} units" + (f" · {'+' if s['roi'] >= 0 else ''}{s['roi']:g}% ROI" if s["roi"] is not None else "")]
    if b:
        lines.append(f"Best winner: {b['sel']}" + (f" @ ${b['price']:.2f}" if b.get("price") else "") + " ✅")
    lines += ["", "Every alert is tracked publicly on overlay.tips (Tracker record), wins and losses.",
              "Link in bio to join 🔗", "", "18+ | Gamble responsibly | Gambling Help 1800 858 858",
              "#overlaytips #sportsbetting #nba #nfl #bettingtips"]
    return "\n".join(lines)


def notify(title, text, img_url):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return
    msg = {"topic": topic, "title": title, "message": text, "tags": ["bar_chart"]}
    if img_url:
        msg["attach"], msg["filename"], msg["click"] = img_url, "overlay-week.png", img_url
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(msg).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def main():
    if "--notify" in sys.argv:   # after the PNG is public: python weekly_graphic.py --notify caption.txt
        cap = open(sys.argv[sys.argv.index("--notify") + 1]).read()
        notify("📸 Weekly results post ready", "Caption (copy + paste):\n\n" + cap, os.environ.get("GRAPHIC_URL", ""))
        return 0
    argv = sys.argv[1:]
    we = None
    if "--week-ending" in argv:
        i = argv.index("--week-ending")
        we = argv[i + 1] if i + 1 < len(argv) else None
        argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith("--")]
    src = args[0] if args else SRC
    out = args[1] if len(args) > 1 else "overlay-week.png"
    today = datetime.now(MEL).date()
    end = today - timedelta(days=today.weekday() + 1)   # last Sunday
    if we:
        end = date.fromisoformat(we)
    rec = load(src)
    start, bets, clv, prices = week_of(rec, end)
    s = summary(bets, clv, prices)
    if not s["n"]:
        print(f"no graded bets {start}..{end}: no graphic")
        return 1
    draw(s, bets, start, end, out)
    cap = caption(s, start, end)
    open(out.rsplit(".", 1)[0] + ".txt", "w").write(cap)
    print(f"graphic: {out} ({s['w']}-{s['l']}, {s['units']:+g}U)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
