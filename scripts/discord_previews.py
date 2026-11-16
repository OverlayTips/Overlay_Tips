"""Posts the newest Overlay Wire preview (previews/posts/*.md) into #previews in the Discord.

Runs when a preview is pushed to main (.github/workflows/discord-previews.yml). Posts the title, the summary,
the first couple of tables from the post (today's games, best prices) as a compact list, and the link.
Never posts the same preview twice: it checks #previews for the post's link first.

Env: DISCORD_BOT_TOKEN, DISCORD_GUILD_ID.
Usage: python scripts/discord_previews.py [slug]   (no slug = the newest post)
"""
import glob, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import discord_post as dp
from build_previews import POSTS, SITE, parse, nice_date

FOOT = "-# Overlay Wire · Price signals, not sure things. 18+ · Gamble responsibly · 1800 858 858"
MAX_TABLES, MAX_ROWS, SHORT = 2, 8, 42   # first column always; others only if every cell is short enough for a phone


def plain(s):
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return s.replace("**", "").strip()


def tables(body):
    """[(heading, header row, rows)] for each pipe table in the post."""
    out, head, cur = [], "", []
    for line in body.splitlines() + [""]:
        t = line.strip()
        if t.startswith("## "):
            head = t[3:].strip()
        if t.startswith("|"):
            if not re.fullmatch(r"\|?[\s:|-]+\|?", t):
                cur.append([plain(c) for c in t.strip("|").split("|")])
        elif cur:
            out.append((head, cur[0], cur[1:]))
            cur = []
    return out


def lines_for(post):
    lines = [post["summary"]]
    for head, cols, rows in tables(post["body"])[:MAX_TABLES]:
        keep = [0] + [i for i in range(1, len(cols)) if all(len(r[i]) <= SHORT for r in rows if i < len(r))]
        if not keep or not rows:
            continue
        lines += ["", f"__{head or 'Today'}__"]
        for r in rows[:MAX_ROWS]:
            cells = [r[i] for i in keep if i < len(r) and r[i]]
            lines.append("• **" + cells[0] + "**" + (" · " + " · ".join(cells[1:]) if cells[1:] else ""))
        if len(rows) > MAX_ROWS:
            lines.append(f"+ {len(rows) - MAX_ROWS} more on the site")
    return lines


def main():
    if not dp.enabled():
        sys.exit("Add the DISCORD_BOT_TOKEN and DISCORD_GUILD_ID secrets first.")
    files = sorted(glob.glob(os.path.join(POSTS, "*.md")))
    if len(sys.argv) > 1 and sys.argv[1].strip():
        files = [f for f in files if os.path.basename(f)[:-3] == sys.argv[1].strip()]
    if not files:
        sys.exit("no preview post found")
    post = max((parse(f) for f in files), key=lambda p: (p["date"], p["slug"]))
    url = f"{SITE}/previews/{post['slug']}.html"
    if dp.posted("previews", url):
        print(f"#previews: {post['slug']} already posted")
        return
    ok = dp.send("previews", f"📰 {post['title']}", lines_for(post), url, FOOT)
    print(f"#previews: {post['slug']} ({nice_date(post['date'])}) {'posted' if ok else 'NOT posted'}")


if __name__ == "__main__":
    main()
