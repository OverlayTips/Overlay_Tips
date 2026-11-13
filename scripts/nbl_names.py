"""Tidy NBL player names. API-Sports writes them as 'Dellavedova Matthew' (surname first) or 'H. Wessels'
(initial only). This turns both into 'Matthew Dellavedova' / 'Harry Wessels' using the NBL27 club rosters
below, and falls back to flipping plain names. Add new signings to ROSTERS as they join."""
import unicodedata

ROSTERS = """
Flynn Cameron, Zylan Cheatham, Bryce Cotton, Ben Griscti, John Jenkins III, Raheim Brown Jnr, Matt Kenyon, Bul Kuol, Mangok Mathiang, Nick Rakocevic, Keanu Rasmussen, Jacob Rigoni, Corey Webster, Isaac White, Harvey White, Deonte Williams,
Rio Bruton, Josh Duach, Tyrell Harrison, Nate Hinton, Jacob Holt, Max Mackinnon, Mawot Mag, Lat Mayen, Sam McDaniel, Billy McRae, Taine Murray, Raul Neto, Mitch Norton, Harry Rouhliadeff, Arnas Velicka, Jaylin Williams, Archie Woodhill,
Jonah Bolden, Jaylon Brown, Walter Brown, Shaun Bruce, Sunday Dech, Jaylin Galloway, Kyrin Galloway, Malique Lewis, Jack McVeigh, Lloyd McVeigh, Brody Nunn, Luke Paul, Keanu Pinder, Reyne Smith, Kobe Williamson, Luca Yates,
Kyle Adnam, Todd Blanchfield, Wani Swaka Lo Buluk, Milton Doyle, Amarco Doyle, Sam Froling, Dan Grida, Tyler Harvey, Davo Hickey, Fletcher MacDonald, Makur Maker, Kelan Martin, Brodie McGregor, Johny Narkle, Lamar Patterson, Mason Peatling, Kobe McDowell-White,
Cole Anthony, Kyle Bowen, Jensen Bradtke, Christian D'Angelo, Jai Fa'ale, Chris Goulding, Will Hynes, Shea Ili, Joe Ingles, Fabijan Krslovic, Sean Macdonald, Malith Machar, Josh Oduro, Austin Shelley, Luke Travers, Sam Waardenburg, Tom Wilson,
Charles Bediako, Aubrey Chunga, Tukaha Cooper, Carlin Davison, Preston Le Gassick, Parker Jackson-Cartwright, Josiah-Jordan James, Kazlo Evans Jr., Mojave King, Izayah Le'Afa, Sam Mennenga, Kouat Noi, Reuben Te Rangi, Anzac Rissetto, Kael Robinson, Dejan Vasiljevic, Nelson Weidemann,
Angus Brandt, Anthony Dell'Orso, Kristian Doolittle, Alex Higgins-Titsha, Cameron Huefner, Jo Lual-Acuil Jr, Brandon Knight, Tana Kopa, Blake Nielsen, Elijah Pepper, Jaron Rillie, Marley Sam, Jesse Wagstaff, Dylan Windler,
Akech Aliir, Ellis Biggar, Dash Daniels, Sam Davidson, Daniel Foster, Angus Glover, Hunter Goodrick, Jordan Hunter, Tanner Krebs, Skylar Mays, Yannis Morin, Patrick Ryan, D'Shawn Schwartz, Nathan Sobey,
Lachlan Bofinger, Andrew Carr, Xavier Cooks, Torrey Craig, Kendric Davis, Matthew Dellavedova, Bailey Hanson, Will Johnston, Keli Leaupepe, Peitok Machar, Makuach Maluach, Tyler Robertson, Jason Spurgin, Jayden Stone, Harry Wessels,
Taran Armstrong, Ben Ayre, Josh Bannan, Biwali Bayles, Majok Deng, Ben Gold, Bryce Hamilton, Ryley Haywood, David Johnson, Will Magnay, Nick Marshall, Dakota Mathias, Jed Richardson, Kody Stattmann, Nicholas Stoddart
"""
SUFFIX = {"jr", "jnr", "sr", "ii", "iii", "iv"}


def _toks(n):
    s = unicodedata.normalize("NFD", n or "").encode("ascii", "ignore").decode().lower()
    for c in ".'`-":
        s = s.replace(c, " ")
    return [t for t in s.split() if t and t not in SUFFIX]


FULL = [n.strip() for n in ROSTERS.replace("\n", " ").split(",") if n.strip()]
_BY_INIT, _BY_SET = {}, {}
for _n in FULL:
    _t = _toks(_n)
    _BY_SET[frozenset(_t)] = _n
    _k = (_t[0][0], " ".join(_t[1:]))
    _BY_INIT[_k] = None if _k in _BY_INIT else _n          # None = two players share it, don't guess


def fix(name):
    n = (name or "").strip()
    t = _toks(n)
    if len(t) < 2:
        return n
    if n.split()[0].endswith("."):                         # "H. Wessels"
        return _BY_INIT.get((t[0][0], " ".join(t[1:]))) or n
    s = set(t)
    for k, full in _BY_SET.items():                        # "Dellavedova Matthew", "Bolden Jonah Anthony"
        if k <= s:
            return full
    w = n.split()
    return " ".join(w[1:] + w[:1]) if len(w) == 2 else n   # unknown player: assume surname first
