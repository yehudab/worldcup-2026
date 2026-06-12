"""Team-name normalization.

Users (and the bot) may refer to a team in Hebrew, by a colloquial name, or by
a spelling variant. ``resolve`` maps such input to the exact canonical name
used by the openfootball data, given the set of known team names.

The bot's playbook already translates Hebrew to English before calling, so this
is mostly a safety net — but it also lets the service work if a raw Hebrew or
variant name slips through.
"""

# Aliases (lowercased) -> canonical openfootball name. Covers the 48 teams of
# the 2026 tournament with Hebrew names and common English variants.
ALIASES = {
    # Group A
    "מקסיקו": "Mexico",
    "דרום אפריקה": "South Africa",
    "דרום קוריאה": "South Korea", "קוריאה": "South Korea", "korea": "South Korea",
    "korea republic": "South Korea", "south korea": "South Korea",
    "צ'כיה": "Czech Republic", "צ׳כיה": "Czech Republic", "czechia": "Czech Republic",
    # Group B
    "קנדה": "Canada",
    "בוסניה": "Bosnia & Herzegovina", "בוסניה הרצגובינה": "Bosnia & Herzegovina",
    "bosnia": "Bosnia & Herzegovina", "bosnia and herzegovina": "Bosnia & Herzegovina",
    "קטאר": "Qatar", "קטר": "Qatar",
    "שווייץ": "Switzerland", "שוויץ": "Switzerland",
    # Group C
    "ברזיל": "Brazil",
    "מרוקו": "Morocco",
    "האיטי": "Haiti",
    "סקוטלנד": "Scotland",
    # Group D
    "ארצות הברית": "USA", "ארהב": "USA", 'ארה"ב': "USA", "usa": "USA",
    "united states": "USA", "united states of america": "USA", "us": "USA",
    "פרגוואי": "Paraguay",
    "אוסטרליה": "Australia",
    "טורקיה": "Turkey", "turkiye": "Turkey", "türkiye": "Turkey",
    # Group E
    "גרמניה": "Germany",
    "קוראסאו": "Curaçao", "curacao": "Curaçao",
    "חוף השנהב": "Ivory Coast", "cote d'ivoire": "Ivory Coast", "côte d'ivoire": "Ivory Coast",
    "אקוודור": "Ecuador",
    # Group F
    "הולנד": "Netherlands", "holland": "Netherlands",
    "יפן": "Japan",
    "שוודיה": "Sweden",
    "תוניסיה": "Tunisia",
    # Group G
    "בלגיה": "Belgium",
    "מצרים": "Egypt",
    "איראן": "Iran", "iran ir": "Iran",
    "ניו זילנד": "New Zealand",
    # Group H
    "ספרד": "Spain",
    "כף ורדה": "Cape Verde", "cabo verde": "Cape Verde",
    "ערב הסעודית": "Saudi Arabia", "saudi": "Saudi Arabia",
    "אורוגוואי": "Uruguay",
    # Group I
    "צרפת": "France",
    "סנגל": "Senegal",
    "עיראק": "Iraq",
    "נורווגיה": "Norway", "norvegia": "Norway",
    # Group J
    "ארגנטינה": "Argentina",
    "אלג'יריה": "Algeria", "אלג׳יריה": "Algeria",
    "אוסטריה": "Austria",
    "ירדן": "Jordan",
    # Group K
    "פורטוגל": "Portugal",
    "קונגו": "DR Congo", "dr congo": "DR Congo", "drc": "DR Congo",
    "congo dr": "DR Congo", "democratic republic of the congo": "DR Congo",
    "אוזבקיסטן": "Uzbekistan",
    "קולומביה": "Colombia",
    # Group L
    "אנגליה": "England",
    "קרואטיה": "Croatia",
    "גאנה": "Ghana",
    "פנמה": "Panama",
}


def resolve(query, known):
    """Return the canonical team name from ``known`` that ``query`` refers to.

    ``known`` is an iterable of canonical names (e.g. from the groups file).
    Returns None if no confident match is found.
    """
    if not query:
        return None
    q = query.strip()
    known = list(known)
    lower_to_canon = {k.lower(): k for k in known}

    # 1. Exact (case-insensitive) match against a known name.
    if q.lower() in lower_to_canon:
        return lower_to_canon[q.lower()]

    # 2. Alias map (Hebrew / variants) -> canonical, kept only if it's a real team.
    alias = ALIASES.get(q.lower())
    if alias and alias in known:
        return alias

    # 3. Substring match against known names (e.g. "bosnia" -> "Bosnia & Herzegovina").
    matches = [k for k in known if q.lower() in k.lower() or k.lower() in q.lower()]
    if len(matches) == 1:
        return matches[0]

    return None
