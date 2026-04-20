#!/usr/bin/env python3
"""
Jamestown Foundation — Executive Briefing Dashboard
Fetches RSS feeds and presents concise classified briefs organized by
region and thematic area in an interactive panel.

Publications:
  • Eurasia Daily Monitor  (EDM)
  • China Brief (+ China Brief Notes)  (CB)
  • Terrorism Monitor      (TM)

Run:   python executivebrief.py
Opens: jf_briefing.html in your default browser
"""

import re
import os
import json
import time
import threading
import socketserver
import http.server
import webbrowser
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests
import feedparser
from bs4 import BeautifulSoup

# ── Configuration ──────────────────────────────────────────────────────────────

DAYS          = 30
REFRESH_HOURS = 6
PORT          = 8743
OUTPUT_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jf_briefing.html")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":                  "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":         "en-US,en;q=0.9",
    "Accept-Encoding":         "gzip, deflate, br",
    "Cache-Control":           "no-cache",
    "Pragma":                  "no-cache",
    "Connection":              "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

PUBLICATIONS = {
    "edm": {
        "id":     "edm",
        "label":  "Eurasia Daily Monitor",
        "short":  "EDM",
        "url":    "https://jamestown.org/publications/edm/",
        "accent": "#3b82f6",
    },
    "cb": {
        "id":     "cb",
        "label":  "China Brief",
        "short":  "CB",
        "url":    "https://jamestown.org/publications/cb/",
        "accent": "#ef4444",
    },
    "tm": {
        "id":     "tm",
        "label":  "Terrorism Monitor",
        "short":  "TM",
        "url":    "https://jamestown.org/publications/tm/",
        "accent": "#f59e0b",
    },
}

# The publication-specific feeds are Cloudflare-protected (403).
# The main sitewide feed is open and contains all three publications.
MAIN_FEED = "https://jamestown.org/feed/"

# CB and TM articles include the publication name in their text.
# EDM articles do not, so they are the fallback after both checks.
# Tags from the RSS entry (e.g. "Al Shabaab", "IRGC") help distinguish
# TM articles that don't self-identify by name.
_TM_TAG_SIGNALS = {
    "al shabaab", "al-shabaab", "al qaeda", "al-qaeda", "houthis", "isis",
    "isil", "islamic state", "boko haram", "terrorism", "jihadist", "militant",
    "insurgent", "salafi", "takfiri", "irgc", "hezbollah", "hamas",
}
_CB_TAG_SIGNALS = {
    "ccp", "pla", "cmc", "prc", "xinjiang", "tibet", "hong kong",
    "taiwan strait", "us-china", "xi jinping",
}

def _detect_pub(text: str, tags: list[str] | None = None) -> str:
    t = text.lower()
    if "china brief" in t:
        return "cb"
    if "terrorism monitor" in t:
        return "tm"
    # Fall back to tag signals when the publication name isn't in the text
    if tags:
        tags_lower = {tag.lower() for tag in tags}
        if tags_lower & _TM_TAG_SIGNALS:
            return "tm"
        if tags_lower & _CB_TAG_SIGNALS:
            return "cb"
    return "edm"

# ── Country keyword → ISO-3166 alpha-3 ────────────────────────────────────────

COUNTRY_MAP = {
    "Russia": "RUS", "Russian": "RUS", "Russians": "RUS",
    "Kremlin": "RUS", "Moscow": "RUS", "Putin": "RUS",
    "Soviet": "RUS", "FSB": "RUS", "GRU": "RUS",
    "SVR": "RUS", "Shoigu": "RUS", "Medvedev": "RUS",

    "Ukraine": "UKR", "Ukrainian": "UKR", "Ukrainians": "UKR",
    "Kyiv": "UKR", "Zelensky": "UKR", "Zelenskyy": "UKR",
    "Donbas": "UKR", "Donbass": "UKR", "Mariupol": "UKR",
    "Kharkiv": "UKR", "Kherson": "UKR", "Zaporizhzhia": "UKR",
    "Crimea": "UKR", "Odesa": "UKR",

    "Belarus": "BLR", "Belarusian": "BLR", "Minsk": "BLR", "Lukashenko": "BLR",
    "Moldova": "MDA", "Moldovan": "MDA", "Chisinau": "MDA", "Transnistria": "MDA",

    "Georgia": "GEO", "Georgian": "GEO", "Tbilisi": "GEO",
    "Abkhazia": "GEO", "South Ossetia": "GEO",
    "Armenia": "ARM", "Armenian": "ARM", "Yerevan": "ARM",
    "Nagorno-Karabakh": "AZE", "Karabakh": "AZE",
    "Azerbaijan": "AZE", "Azerbaijani": "AZE", "Baku": "AZE", "Aliyev": "AZE",

    "Kazakhstan": "KAZ", "Kazakh": "KAZ", "Astana": "KAZ", "Tokayev": "KAZ",
    "Uzbekistan": "UZB", "Uzbek": "UZB", "Tashkent": "UZB",
    "Kyrgyzstan": "KGZ", "Kyrgyz": "KGZ", "Bishkek": "KGZ",
    "Tajikistan": "TJK", "Tajik": "TJK", "Dushanbe": "TJK",
    "Turkmenistan": "TKM", "Turkmen": "TKM", "Ashgabat": "TKM",
    "Mongolia": "MNG", "Mongolian": "MNG", "Ulaanbaatar": "MNG",

    "Poland": "POL", "Polish": "POL", "Warsaw": "POL",
    "Hungary": "HUN", "Hungarian": "HUN", "Budapest": "HUN",
    "Orbán": "HUN", "Fidesz": "HUN",
    "Romania": "ROU", "Romanian": "ROU", "Bucharest": "ROU",
    "Bulgaria": "BGR", "Bulgarian": "BGR", "Sofia": "BGR",
    "Serbia": "SRB", "Serbian": "SRB", "Belgrade": "SRB",
    "Kosovo": "RKS", "Kosovar": "RKS",
    "Bosnia": "BIH", "Bosnian": "BIH", "Sarajevo": "BIH",
    "Croatia": "HRV", "Croatian": "HRV", "Zagreb": "HRV",
    "Slovenia": "SVN", "Slovenian": "SVN",
    "Albania": "ALB", "Albanian": "ALB", "Tirana": "ALB",
    "North Macedonia": "MKD", "Macedonian": "MKD", "Skopje": "MKD",
    "Montenegro": "MNE",
    "Slovakia": "SVK", "Slovak": "SVK", "Bratislava": "SVK",
    "Czech Republic": "CZE", "Czech": "CZE", "Prague": "CZE", "Czechia": "CZE",
    "Lithuania": "LTU", "Lithuanian": "LTU", "Vilnius": "LTU",
    "Latvia": "LVA", "Latvian": "LVA", "Riga": "LVA",
    "Estonia": "EST", "Estonian": "EST", "Tallinn": "EST",

    "Germany": "DEU", "German": "DEU", "Berlin": "DEU",
    "France": "FRA", "French": "FRA", "Paris": "FRA", "Macron": "FRA",
    "United Kingdom": "GBR", "Britain": "GBR", "British": "GBR", "London": "GBR",
    "Italy": "ITA", "Italian": "ITA", "Rome": "ITA", "Meloni": "ITA",
    "Spain": "ESP", "Spanish": "ESP", "Madrid": "ESP",
    "Netherlands": "NLD", "Dutch": "NLD", "Amsterdam": "NLD",
    "Belgium": "BEL", "Belgian": "BEL", "Brussels": "BEL",
    "Austria": "AUT", "Austrian": "AUT", "Vienna": "AUT",
    "Sweden": "SWE", "Swedish": "SWE", "Stockholm": "SWE",
    "Finland": "FIN", "Finnish": "FIN", "Helsinki": "FIN",
    "Denmark": "DNK", "Danish": "DNK", "Copenhagen": "DNK",
    "Norway": "NOR", "Norwegian": "NOR", "Oslo": "NOR",
    "Iceland": "ISL",
    "Switzerland": "CHE", "Swiss": "CHE", "Bern": "CHE",
    "Portugal": "PRT", "Portuguese": "PRT", "Lisbon": "PRT",
    "Greece": "GRC", "Greek": "GRC", "Athens": "GRC",
    "Turkey": "TUR", "Turkish": "TUR", "Ankara": "TUR",
    "Erdoğan": "TUR", "Erdogan": "TUR", "Türkiye": "TUR",

    "Iran": "IRN", "Iranian": "IRN", "Tehran": "IRN",
    "Khamenei": "IRN", "IRGC": "IRN",
    "Iraq": "IRQ", "Iraqi": "IRQ", "Baghdad": "IRQ",
    "Syria": "SYR", "Syrian": "SYR", "Damascus": "SYR", "Assad": "SYR",
    "Israel": "ISR", "Israeli": "ISR", "Tel Aviv": "ISR",
    "Netanyahu": "ISR", "IDF": "ISR",
    "Palestine": "PSE", "Palestinian": "PSE", "Gaza": "PSE",
    "Hamas": "PSE", "West Bank": "PSE",
    "Lebanon": "LBN", "Lebanese": "LBN", "Beirut": "LBN", "Hezbollah": "LBN",
    "Jordan": "JOR", "Jordanian": "JOR", "Amman": "JOR",
    "Saudi Arabia": "SAU", "Saudi": "SAU", "Riyadh": "SAU",
    "UAE": "ARE", "Emirates": "ARE", "Dubai": "ARE", "Abu Dhabi": "ARE",
    "Qatar": "QAT", "Qatari": "QAT", "Doha": "QAT",
    "Kuwait": "KWT", "Bahrain": "BHR", "Oman": "OMN",
    "Yemen": "YEM", "Yemeni": "YEM", "Houthi": "YEM",
    "Libya": "LBY", "Libyan": "LBY", "Tripoli": "LBY",
    "Egypt": "EGY", "Egyptian": "EGY", "Cairo": "EGY",
    "Tunisia": "TUN", "Tunisian": "TUN", "Tunis": "TUN",
    "Algeria": "DZA", "Algerian": "DZA", "Algiers": "DZA",
    "Morocco": "MAR", "Moroccan": "MAR", "Rabat": "MAR",
    "Sudan": "SDN", "Sudanese": "SDN", "Khartoum": "SDN",

    "Nigeria": "NGA", "Nigerian": "NGA", "Abuja": "NGA",
    "Ethiopia": "ETH", "Ethiopian": "ETH", "Addis Ababa": "ETH",
    "Kenya": "KEN", "Kenyan": "KEN", "Nairobi": "KEN",
    "Somalia": "SOM", "Somali": "SOM", "Mogadishu": "SOM",
    "al-Shabaab": "SOM", "Al-Shabaab": "SOM",
    "Mali": "MLI", "Malian": "MLI", "Bamako": "MLI", "Sahel": "MLI",
    "Niger": "NER", "Burkina Faso": "BFA", "Chad": "TCD", "Chadian": "TCD",
    "Cameroon": "CMR",
    "Central African Republic": "CAF", "CAR": "CAF",
    "DR Congo": "COD", "DRC": "COD", "Kinshasa": "COD",
    "Congo": "COG", "Senegal": "SEN", "Guinea": "GIN",
    "South Africa": "ZAF", "Mozambique": "MOZ", "Zimbabwe": "ZWE",
    "Angola": "AGO", "Tanzania": "TZA", "Uganda": "UGA", "Rwanda": "RWA",
    "Eritrea": "ERI", "Djibouti": "DJI", "Gambia": "GMB",
    "Togo": "TGO", "Benin": "BEN", "Ghana": "GHA", "Ivory Coast": "CIV",

    "Afghanistan": "AFG", "Afghan": "AFG", "Taliban": "AFG", "Kabul": "AFG",
    "Pakistan": "PAK", "Pakistani": "PAK", "Islamabad": "PAK",
    "India": "IND", "Indian": "IND", "New Delhi": "IND", "Modi": "IND",
    "Bangladesh": "BGD", "Bangladeshi": "BGD", "Dhaka": "BGD",
    "Sri Lanka": "LKA", "Nepal": "NPL", "Nepalese": "NPL", "Kathmandu": "NPL",
    "Myanmar": "MMR", "Burma": "MMR", "Burmese": "MMR",
    "Thailand": "THA", "Thai": "THA", "Bangkok": "THA",
    "Vietnam": "VNM", "Vietnamese": "VNM", "Hanoi": "VNM",
    "Indonesia": "IDN", "Indonesian": "IDN", "Jakarta": "IDN",
    "Philippines": "PHL", "Filipino": "PHL", "Manila": "PHL",
    "Malaysia": "MYS", "Malaysian": "MYS", "Kuala Lumpur": "MYS",
    "Singapore": "SGP", "Cambodia": "KHM", "Laos": "LAO",

    "China": "CHN", "Chinese": "CHN", "Beijing": "CHN",
    "CCP": "CHN", "Xi Jinping": "CHN", "PRC": "CHN",
    "PLA": "CHN", "Xinjiang": "CHN", "Tibet": "CHN",
    "Hong Kong": "CHN", "Huawei": "CHN",
    "Taiwan": "TWN", "Taiwanese": "TWN", "Taipei": "TWN",
    "North Korea": "PRK", "DPRK": "PRK", "Pyongyang": "PRK", "Kim Jong": "PRK",
    "South Korea": "KOR", "Seoul": "KOR",
    "Japan": "JPN", "Japanese": "JPN", "Tokyo": "JPN",

    "United States": "USA", "American": "USA", "Washington": "USA",
    "Pentagon": "USA", "Biden": "USA", "Trump": "USA", "Congress": "USA",
    "Canada": "CAN", "Canadian": "CAN", "Ottawa": "CAN",
    "Mexico": "MEX", "Mexican": "MEX", "Mexico City": "MEX",
    "Venezuela": "VEN", "Venezuelan": "VEN", "Maduro": "VEN",
    "Cuba": "CUB", "Cuban": "CUB", "Havana": "CUB",
    "Nicaragua": "NIC", "Nicaraguan": "NIC", "Ortega": "NIC",
    "Colombia": "COL", "Colombian": "COL",
    "Brazil": "BRA", "Brazilian": "BRA",
    "Argentina": "ARG", "Argentine": "ARG",
    "Chile": "CHL", "Peru": "PER", "Panama": "PAN",

    "Australia": "AUS", "Australian": "AUS", "Canberra": "AUS",
    "New Zealand": "NZL", "Greenland": "GRL",
    "Islamic State": "IRQ", "ISIS": "IRQ", "ISIL": "IRQ",
    "al-Qaeda": "AFG", "Al-Qaeda": "AFG",
}

SORTED_KEYWORDS = sorted(COUNTRY_MAP.keys(), key=len, reverse=True)

DISPLAY_NAMES = {
    "RUS": "Russia", "UKR": "Ukraine", "BLR": "Belarus", "MDA": "Moldova",
    "GEO": "Georgia", "ARM": "Armenia", "AZE": "Azerbaijan",
    "KAZ": "Kazakhstan", "UZB": "Uzbekistan", "KGZ": "Kyrgyzstan",
    "TJK": "Tajikistan", "TKM": "Turkmenistan", "MNG": "Mongolia",
    "POL": "Poland", "HUN": "Hungary", "ROU": "Romania", "BGR": "Bulgaria",
    "SRB": "Serbia", "RKS": "Kosovo", "BIH": "Bosnia",
    "HRV": "Croatia", "SVN": "Slovenia", "ALB": "Albania", "MKD": "N. Macedonia",
    "MNE": "Montenegro", "SVK": "Slovakia", "CZE": "Czech Republic",
    "LTU": "Lithuania", "LVA": "Latvia", "EST": "Estonia",
    "DEU": "Germany", "FRA": "France", "GBR": "UK",
    "ITA": "Italy", "ESP": "Spain", "NLD": "Netherlands", "BEL": "Belgium",
    "AUT": "Austria", "SWE": "Sweden", "FIN": "Finland", "DNK": "Denmark",
    "NOR": "Norway", "ISL": "Iceland", "CHE": "Switzerland",
    "PRT": "Portugal", "GRC": "Greece", "TUR": "Turkey",
    "IRN": "Iran", "IRQ": "Iraq", "SYR": "Syria", "ISR": "Israel",
    "PSE": "Palestine", "LBN": "Lebanon", "JOR": "Jordan", "SAU": "Saudi Arabia",
    "ARE": "UAE", "QAT": "Qatar", "KWT": "Kuwait", "BHR": "Bahrain",
    "OMN": "Oman", "YEM": "Yemen", "LBY": "Libya", "EGY": "Egypt",
    "TUN": "Tunisia", "DZA": "Algeria", "MAR": "Morocco", "SDN": "Sudan",
    "NGA": "Nigeria", "ETH": "Ethiopia", "KEN": "Kenya", "SOM": "Somalia",
    "MLI": "Mali", "NER": "Niger", "BFA": "Burkina Faso", "TCD": "Chad",
    "CMR": "Cameroon", "CAF": "C.A.R.",
    "COD": "DR Congo", "COG": "Congo", "SEN": "Senegal", "GIN": "Guinea",
    "ZAF": "South Africa", "MOZ": "Mozambique", "ZWE": "Zimbabwe",
    "AGO": "Angola", "TZA": "Tanzania", "UGA": "Uganda", "RWA": "Rwanda",
    "ERI": "Eritrea", "DJI": "Djibouti", "GMB": "Gambia", "TGO": "Togo",
    "BEN": "Benin", "GHA": "Ghana", "CIV": "Côte d'Ivoire",
    "AFG": "Afghanistan", "PAK": "Pakistan", "IND": "India",
    "BGD": "Bangladesh", "LKA": "Sri Lanka", "NPL": "Nepal",
    "MMR": "Myanmar", "THA": "Thailand", "VNM": "Vietnam",
    "IDN": "Indonesia", "PHL": "Philippines", "MYS": "Malaysia",
    "SGP": "Singapore", "KHM": "Cambodia", "LAO": "Laos",
    "CHN": "China", "TWN": "Taiwan", "PRK": "North Korea",
    "KOR": "South Korea", "JPN": "Japan",
    "USA": "United States", "CAN": "Canada", "MEX": "Mexico",
    "VEN": "Venezuela", "CUB": "Cuba", "NIC": "Nicaragua",
    "COL": "Colombia", "BRA": "Brazil", "ARG": "Argentina",
    "CHL": "Chile", "PER": "Peru", "PAN": "Panama",
    "AUS": "Australia", "NZL": "New Zealand", "GRL": "Greenland",
}

# ── Region mapping ────────────────────────────────────────────────────────────

REGIONS = {
    "Russia":              ["RUS"],
    "Eastern Europe":      ["UKR", "BLR", "MDA", "POL", "HUN", "ROU", "BGR",
                            "SRB", "RKS", "BIH", "HRV", "SVN", "ALB", "MKD",
                            "MNE", "SVK", "CZE", "LTU", "LVA", "EST"],
    "Caucasus":            ["GEO", "ARM", "AZE"],
    "Central Asia":        ["KAZ", "UZB", "KGZ", "TJK", "TKM", "MNG"],
    "Western Europe":      ["DEU", "FRA", "GBR", "ITA", "ESP", "NLD", "BEL",
                            "AUT", "SWE", "FIN", "DNK", "NOR", "ISL", "CHE",
                            "PRT", "GRC", "TUR"],
    "Middle East":         ["IRN", "IRQ", "SYR", "ISR", "PSE", "LBN", "JOR",
                            "SAU", "ARE", "QAT", "KWT", "BHR", "OMN", "YEM"],
    "North Africa":        ["LBY", "EGY", "TUN", "DZA", "MAR", "SDN"],
    "Sub-Saharan Africa":  ["NGA", "ETH", "KEN", "SOM", "MLI", "NER", "BFA",
                            "TCD", "CMR", "CAF", "COD", "COG", "SEN", "GIN",
                            "ZAF", "MOZ", "ZWE", "AGO", "TZA", "UGA", "RWA",
                            "ERI", "DJI", "GMB", "TGO", "BEN", "GHA", "CIV"],
    "South Asia":          ["AFG", "PAK", "IND", "BGD", "LKA", "NPL"],
    "Southeast Asia":      ["MMR", "THA", "VNM", "IDN", "PHL", "MYS", "SGP",
                            "KHM", "LAO"],
    "East Asia":           ["CHN", "TWN", "PRK", "KOR", "JPN"],
    "Americas":            ["USA", "CAN", "MEX", "VEN", "CUB", "NIC", "COL",
                            "BRA", "ARG", "CHL", "PER", "PAN"],
    "Oceania":             ["AUS", "NZL"],
}

ISO_TO_REGION: dict[str, str] = {}
for _region, _codes in REGIONS.items():
    for _code in _codes:
        ISO_TO_REGION[_code] = _region

# ── Thematic classification ───────────────────────────────────────────────────

THEMES: dict[str, list[str]] = {
    "Military & Defense": [
        "military", "troops", "forces", "weapons", "missile", "defense", "NATO",
        "army", "navy", "air force", "combat", "offensive", "frontline", "front line",
        "artillery", "armor", "armour", "drone", "UAV", "munitions", "ammunition",
        "tank", "fighter jet", "warplane", "warship", "submarine", "battalion",
        "regiment", "brigade", "soldier", "casualt", "killed in action", "wounded",
        "occupation", "invasion", "ceasefire", "truce", "counter-offensive",
    ],
    "Intelligence & Security": [
        "intelligence", "CIA", "FSB", "GRU", "SVR", "spy", "espionage",
        "surveillance", "covert", "clandestine", "counterintelligence",
        "security service", "secret service", "operative", "station chief",
        "defect", "leak", "classified", "signals intelligence", "SIGINT", "HUMINT",
    ],
    "Political Affairs": [
        "election", "government", "president", "parliament", "minister", "political",
        "opposition", "protest", "coup", "policy", "diplomacy", "diplomatic",
        "treaty", "summit", "negotiat", "alliance", "coalition", "authoritarian",
        "democracy", "regime", "leader", "party", "vote", "referendum",
        "geopolit", "foreign policy", "sanction",
    ],
    "Economic Affairs": [
        "economy", "economic", "trade", "GDP", "investment", "budget", "currency",
        "inflation", "export", "import", "financial", "bank", "debt", "recession",
        "market", "supply chain", "tariff", "commerce", "industrial",
    ],
    "Energy & Resources": [
        "energy", "oil", "gas", "pipeline", "LNG", "petroleum", "coal", "renewable",
        "electricity", "power grid", "mineral", "rare earth", "lithium",
        "Nord Stream", "Turkstream", "OPEC", "refinery", "drilling",
    ],
    "Terrorism & Extremism": [
        "terrorist", "terrorism", "extremi", "jihadist", "insurgent", "attack",
        "bomb", "suicide bomber", "militant", "radical", "ISIS", "ISIL",
        "Islamic State", "al-Qaeda", "al-Shabaab", "Boko Haram", "salafi",
        "takfiri", "jihad", "recruitment", "financing terror", "cell", "plot",
        "IED", "ambush",
    ],
    "Nuclear & WMD": [
        "nuclear", "warhead", "ICBM", "ballistic missile", "chemical weapon",
        "biological weapon", "WMD", "nonproliferation", "disarmament", "fissile",
        "enrichment", "reactor", "uranium", "plutonium", "radiological", "CBRN",
        "weapon of mass destruction",
    ],
    "Cyber & Disinformation": [
        "cyber", "hack", "malware", "ransomware", "disinformation", "propaganda",
        "information warfare", "influence operation", "social media", "bot",
        "fake news", "narrative", "dezinformatsiya", "active measures", "troll",
        "information space", "psychological operation", "PSYOP", "deepfake",
    ],
}

THEME_ICONS: dict[str, str] = {
    "Military & Defense":      "⚔",
    "Intelligence & Security": "🔍",
    "Political Affairs":       "🏛",
    "Economic Affairs":        "📊",
    "Energy & Resources":      "⚡",
    "Terrorism & Extremism":   "🎯",
    "Nuclear & WMD":           "☢",
    "Cyber & Disinformation":  "💻",
}

REGION_ICONS: dict[str, str] = {
    "Russia":             "🇷🇺",
    "Eastern Europe":     "🌍",
    "Caucasus":           "⛰",
    "Central Asia":       "🏔",
    "Western Europe":     "🇪🇺",
    "Middle East":        "🌙",
    "North Africa":       "🏜",
    "Sub-Saharan Africa": "🌍",
    "South Asia":         "🌏",
    "Southeast Asia":     "🌴",
    "East Asia":          "🐉",
    "Americas":           "🌎",
    "Oceania":            "🌊",
}

# ── RSS fetching ──────────────────────────────────────────────────────────────

def _extract_brief(text: str, max_sentences: int = 3, max_chars: int = 400) -> str:
    """Extract first 2-3 clean sentences as an executive brief."""
    text = re.sub(r'\s+', ' ', text).strip()
    sentences = re.split(r'(?<=[.!?])\s+', text)
    out = []
    total = 0
    for sent in sentences:
        sent = sent.strip()
        if not sent or len(sent) < 20:
            continue
        if total + len(sent) > max_chars and out:
            break
        out.append(sent)
        total += len(sent)
        if len(out) >= max_sentences:
            break
    result = " ".join(out)
    if len(result) > max_chars:
        result = result[:max_chars].rsplit(" ", 1)[0] + "…"
    return result


def fetch_all_articles(days: int = DAYS) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    session = requests.Session()
    session.headers.update(HEADERS)
    all_articles: list[dict] = []
    seen_urls: set[str] = set()

    print(f"\nFetching from {MAIN_FEED} (last {days} days)…")
    page = 1
    while page <= 50:
        url = f"{MAIN_FEED}?paged={page}"
        resp = None
        for attempt in range(3):
            try:
                if attempt > 0:
                    time.sleep(2 ** attempt)
                resp = session.get(url, timeout=25)
                resp.raise_for_status()
                break
            except requests.RequestException as exc:
                if attempt == 2:
                    print(f"  Warning: page {page} failed ({exc})")
        if resp is None or not resp.ok:
            break

        feed = feedparser.parse(resp.text)
        if not feed.entries:
            break

        reached_cutoff = False
        for entry in feed.entries:
            if not getattr(entry, "published_parsed", None):
                continue
            pub_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            if pub_dt < cutoff:
                reached_cutoff = True
                break
            if entry.link in seen_urls:
                continue
            seen_urls.add(entry.link)

            content_html = ""
            if hasattr(entry, "content") and entry.content:
                content_html = entry.content[0].value
            elif hasattr(entry, "summary"):
                content_html = entry.summary

            full_text  = BeautifulSoup(content_html, "html.parser").get_text(" ")
            brief      = _extract_brief(full_text)
            entry_tags = [t.term for t in getattr(entry, "tags", [])]
            pub_id     = _detect_pub(entry.title + " " + full_text, entry_tags)
            pub       = PUBLICATIONS[pub_id]

            all_articles.append({
                "title":      entry.title.strip(),
                "date":       pub_dt.strftime("%Y-%m-%d"),
                "ts":         pub_dt.timestamp(),
                "url":        entry.link,
                "brief":      brief,
                "full_text":  entry.title + " " + full_text,
                "pub":        pub_id,
                "pub_label":  pub["label"],
                "pub_short":  pub["short"],
                "pub_accent": pub["accent"],
            })

        print(f"  Page {page}: {len(all_articles)} articles so far")
        if reached_cutoff:
            break
        page += 1

    all_articles.sort(key=lambda a: a["ts"], reverse=True)
    print(f"\nTotal articles fetched: {len(all_articles)}")
    return all_articles


# ── Classification ────────────────────────────────────────────────────────────

def classify_article(art: dict) -> dict:
    text = art["full_text"]

    # Country detection
    countries: set[str] = set()
    for keyword in SORTED_KEYWORDS:
        pattern = r"\b" + re.escape(keyword) + r"\b"
        if re.search(pattern, text, re.IGNORECASE):
            countries.add(COUNTRY_MAP[keyword])

    # Region detection
    regions: set[str] = set()
    for iso in countries:
        region = ISO_TO_REGION.get(iso)
        if region:
            regions.add(region)

    # Theme detection
    text_lower = text.lower()
    themes: list[str] = []
    for theme, keywords in THEMES.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                themes.append(theme)
                break

    country_names = sorted(
        {DISPLAY_NAMES.get(iso, iso) for iso in countries},
        key=lambda n: n
    )

    return {
        **art,
        "countries":     sorted(countries),
        "country_names": country_names,
        "regions":       sorted(regions),
        "themes":        themes,
    }


# ── HTML generation ───────────────────────────────────────────────────────────

def build_html(articles: list[dict], days: int) -> str:
    generated_at = datetime.now().strftime("%B %d, %Y at %H:%M")
    articles_json = json.dumps(articles, ensure_ascii=False)
    pub_meta_json  = json.dumps({
        pid: {"label": p["label"], "short": p["short"], "accent": p["accent"]}
        for pid, p in PUBLICATIONS.items()
    })
    region_icons_json = json.dumps(REGION_ICONS)
    theme_icons_json  = json.dumps(THEME_ICONS)
    themes_list_json  = json.dumps(list(THEMES.keys()))
    regions_list_json = json.dumps(list(REGIONS.keys()))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jamestown Foundation — Executive Briefing</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Playfair+Display:wght@600;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg:         #0b0f14;
    --surface:    #131920;
    --surface2:   #1a2230;
    --border:     #1e2d40;
    --border2:    #243347;
    --text:       #e2e8f0;
    --text-muted: #6b7e96;
    --text-dim:   #3d5068;
    --accent:     #3b82f6;
    --sidebar-w:  280px;
    --header-h:   60px;
  }}

  html, body {{
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', sans-serif;
    font-size: 14px;
    line-height: 1.5;
    overflow: hidden;
  }}

  /* ── Header ── */
  .header {{
    position: fixed;
    top: 0; left: 0; right: 0;
    height: var(--header-h);
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    padding: 0 20px;
    gap: 16px;
    z-index: 100;
  }}
  .header-logo {{
    font-family: 'Playfair Display', serif;
    font-size: 17px;
    font-weight: 700;
    color: var(--text);
    letter-spacing: .01em;
    white-space: nowrap;
    flex-shrink: 0;
  }}
  .header-logo span {{
    color: var(--accent);
  }}
  .header-divider {{
    width: 1px;
    height: 24px;
    background: var(--border2);
    flex-shrink: 0;
  }}
  .header-meta {{
    font-size: 12px;
    color: var(--text-muted);
    flex-shrink: 0;
  }}
  .header-spacer {{ flex: 1; }}

  .search-wrap {{
    position: relative;
    flex: 0 1 320px;
  }}
  .search-wrap svg {{
    position: absolute;
    left: 10px; top: 50%;
    transform: translateY(-50%);
    color: var(--text-dim);
    pointer-events: none;
  }}
  #search {{
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border2);
    border-radius: 6px;
    padding: 6px 10px 6px 32px;
    color: var(--text);
    font-size: 13px;
    outline: none;
    transition: border-color .15s;
  }}
  #search:focus {{ border-color: var(--accent); }}
  #search::placeholder {{ color: var(--text-dim); }}

  .stat-pill {{
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 3px 10px;
    font-size: 12px;
    color: var(--text-muted);
    white-space: nowrap;
    flex-shrink: 0;
  }}
  .stat-pill b {{ color: var(--text); }}

  /* ── Layout ── */
  .layout {{
    display: flex;
    height: 100vh;
    padding-top: var(--header-h);
  }}

  /* ── Sidebar ── */
  .sidebar {{
    width: var(--sidebar-w);
    flex-shrink: 0;
    background: var(--surface);
    border-right: 1px solid var(--border);
    overflow-y: auto;
    padding: 16px 0 24px;
  }}
  .sidebar::-webkit-scrollbar {{ width: 4px; }}
  .sidebar::-webkit-scrollbar-track {{ background: transparent; }}
  .sidebar::-webkit-scrollbar-thumb {{ background: var(--border2); border-radius: 2px; }}

  .sidebar-section {{
    margin-bottom: 4px;
  }}
  .sidebar-heading {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: var(--text-dim);
    padding: 14px 16px 6px;
  }}
  .filter-row {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 16px;
    cursor: pointer;
    border-radius: 0;
    transition: background .1s;
    user-select: none;
  }}
  .filter-row:hover {{ background: var(--surface2); }}
  .filter-row.active {{
    background: rgba(59,130,246,.12);
  }}
  .filter-icon {{ font-size: 14px; flex-shrink: 0; width: 20px; text-align: center; }}
  .filter-label {{
    font-size: 13px;
    color: var(--text-muted);
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    transition: color .1s;
  }}
  .filter-row.active .filter-label {{ color: var(--text); font-weight: 500; }}
  .filter-count {{
    font-size: 11px;
    color: var(--text-dim);
    background: var(--surface2);
    border-radius: 10px;
    padding: 1px 7px;
    flex-shrink: 0;
  }}
  .filter-row.active .filter-count {{
    background: rgba(59,130,246,.2);
    color: #93c5fd;
  }}

  .pub-dot {{
    width: 8px; height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }}

  /* ── Main ── */
  .main {{
    flex: 1;
    overflow-y: auto;
    padding: 20px 24px 40px;
    display: flex;
    flex-direction: column;
    gap: 0;
  }}
  .main::-webkit-scrollbar {{ width: 6px; }}
  .main::-webkit-scrollbar-track {{ background: transparent; }}
  .main::-webkit-scrollbar-thumb {{ background: var(--border2); border-radius: 3px; }}

  .section-banner {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 16px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border);
  }}
  .section-title {{
    font-family: 'Playfair Display', serif;
    font-size: 20px;
    font-weight: 700;
    color: var(--text);
  }}
  .section-count {{
    font-size: 12px;
    color: var(--text-muted);
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 2px 9px;
  }}
  .no-results {{
    text-align: center;
    padding: 80px 20px;
    color: var(--text-muted);
  }}
  .no-results-icon {{
    font-size: 40px;
    margin-bottom: 12px;
  }}

  /* ── Article cards ── */
  .cards-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 14px;
    margin-bottom: 32px;
  }}

  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px 14px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    transition: border-color .15s, background .15s, transform .15s;
    cursor: default;
  }}
  .card:hover {{
    border-color: var(--border2);
    background: var(--surface2);
    transform: translateY(-1px);
  }}

  .card-header {{
    display: flex;
    align-items: flex-start;
    gap: 8px;
  }}
  .pub-badge {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .05em;
    padding: 2px 7px;
    border-radius: 4px;
    flex-shrink: 0;
    margin-top: 1px;
  }}
  .card-date {{
    font-size: 11px;
    color: var(--text-dim);
    flex-shrink: 0;
    margin-top: 2px;
    margin-left: auto;
  }}

  .card-title {{
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
    line-height: 1.4;
  }}
  .card-title a {{
    color: inherit;
    text-decoration: none;
    transition: color .12s;
  }}
  .card-title a:hover {{ color: #93c5fd; }}

  .card-brief {{
    font-size: 12.5px;
    color: var(--text-muted);
    line-height: 1.6;
    flex: 1;
  }}

  .card-tags {{
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }}
  .tag {{
    font-size: 10.5px;
    padding: 2px 8px;
    border-radius: 4px;
    border: 1px solid transparent;
    white-space: nowrap;
    cursor: pointer;
    transition: opacity .12s;
  }}
  .tag:hover {{ opacity: .75; }}
  .tag-region {{
    background: rgba(99,102,241,.12);
    border-color: rgba(99,102,241,.25);
    color: #a5b4fc;
  }}
  .tag-theme {{
    background: rgba(16,185,129,.10);
    border-color: rgba(16,185,129,.22);
    color: #6ee7b7;
  }}
  .tag-country {{
    background: rgba(245,158,11,.08);
    border-color: rgba(245,158,11,.18);
    color: #fcd34d;
  }}

  .card-footer {{
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    padding-top: 6px;
    border-top: 1px solid var(--border);
  }}
  .read-link {{
    font-size: 11.5px;
    font-weight: 500;
    color: var(--accent);
    text-decoration: none;
    padding: 3px 10px;
    border-radius: 5px;
    border: 1px solid rgba(59,130,246,.3);
    transition: background .12s, border-color .12s;
  }}
  .read-link:hover {{
    background: rgba(59,130,246,.12);
    border-color: rgba(59,130,246,.5);
  }}

  /* ── Region group header ── */
  .region-group {{ margin-bottom: 8px; }}
  .region-header {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 0 12px;
    cursor: pointer;
    user-select: none;
  }}
  .region-header-icon {{ font-size: 18px; }}
  .region-header-name {{
    font-family: 'Playfair Display', serif;
    font-size: 16px;
    font-weight: 600;
    color: var(--text);
  }}
  .region-header-count {{
    font-size: 12px;
    color: var(--text-muted);
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1px 8px;
  }}
  .region-header-toggle {{
    margin-left: auto;
    color: var(--text-dim);
    font-size: 12px;
    transition: transform .2s;
  }}
  .region-group.collapsed .region-header-toggle {{ transform: rotate(-90deg); }}
  .region-group.collapsed .cards-grid {{ display: none; }}

  /* ── Scrollbar ── */
  * {{ scrollbar-width: thin; scrollbar-color: var(--border2) transparent; }}
</style>
</head>
<body>

<header class="header">
  <div class="header-logo">Jamestown Foundation <span>Briefings</span></div>
  <div class="header-divider"></div>
  <div class="header-meta">Generated {generated_at} · Last {days} days</div>
  <div class="header-spacer"></div>
  <div class="search-wrap">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
    <input id="search" type="text" placeholder="Search articles…" autocomplete="off">
  </div>
  <div class="stat-pill" id="article-count-pill">Loading…</div>
</header>

<div class="layout">
  <nav class="sidebar" id="sidebar">
    <div class="sidebar-section">
      <div class="sidebar-heading">View</div>
      <div class="filter-row active" data-filter-type="all" data-filter-value="all">
        <span class="filter-icon">📋</span>
        <span class="filter-label">All Articles</span>
        <span class="filter-count" id="count-all">0</span>
      </div>
    </div>

    <div class="sidebar-section">
      <div class="sidebar-heading">Publication</div>
      <div id="pub-filters"></div>
    </div>

    <div class="sidebar-section">
      <div class="sidebar-heading">Region</div>
      <div id="region-filters"></div>
    </div>

    <div class="sidebar-section">
      <div class="sidebar-heading">Theme</div>
      <div id="theme-filters"></div>
    </div>
  </nav>

  <main class="main" id="main">
    <div class="section-banner">
      <div class="section-title" id="section-title">Loading briefings…</div>
      <div class="section-count" id="section-count"></div>
    </div>
    <div id="content"></div>
  </main>
</div>

<script>
const ARTICLES      = {articles_json};
const PUB_META      = {pub_meta_json};
const REGION_ICONS  = {region_icons_json};
const THEME_ICONS   = {theme_icons_json};
const ALL_THEMES    = {themes_list_json};
const ALL_REGIONS   = {regions_list_json};

// ── State ──────────────────────────────────────────────────────────────────

let activeFilter  = {{ type: 'all', value: 'all' }};
let searchQuery   = '';
let collapsedRegions = new Set();

// ── Helpers ────────────────────────────────────────────────────────────────

function fmtDate(d) {{
  const [y, m, day] = d.split('-');
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${{months[+m-1]}} ${{+day}}, ${{y}}`;
}}

function filterArticles(articles) {{
  let list = articles;
  if (activeFilter.type !== 'all') {{
    list = list.filter(a => {{
      if (activeFilter.type === 'pub')    return a.pub === activeFilter.value;
      if (activeFilter.type === 'region') return a.regions.includes(activeFilter.value);
      if (activeFilter.type === 'theme')  return a.themes.includes(activeFilter.value);
      return true;
    }});
  }}
  if (searchQuery) {{
    const q = searchQuery.toLowerCase();
    list = list.filter(a =>
      a.title.toLowerCase().includes(q) ||
      a.brief.toLowerCase().includes(q) ||
      a.regions.join(' ').toLowerCase().includes(q) ||
      a.themes.join(' ').toLowerCase().includes(q) ||
      a.country_names.join(' ').toLowerCase().includes(q)
    );
  }}
  return list;
}}

// ── Sidebar build ──────────────────────────────────────────────────────────

function buildSidebar() {{
  // Publications
  const pubDiv = document.getElementById('pub-filters');
  pubDiv.innerHTML = '';
  Object.entries(PUB_META).forEach(([pid, pub]) => {{
    const count = ARTICLES.filter(a => a.pub === pid).length;
    if (!count) return;
    const row = document.createElement('div');
    row.className = 'filter-row';
    row.dataset.filterType  = 'pub';
    row.dataset.filterValue = pid;
    row.innerHTML = `
      <span class="pub-dot" style="background:${{pub.accent}}"></span>
      <span class="filter-label">${{pub.label}}</span>
      <span class="filter-count" id="count-pub-${{pid}}">${{count}}</span>`;
    row.addEventListener('click', () => setFilter('pub', pid));
    pubDiv.appendChild(row);
  }});

  // Regions
  const regionDiv = document.getElementById('region-filters');
  regionDiv.innerHTML = '';
  ALL_REGIONS.forEach(region => {{
    const count = ARTICLES.filter(a => a.regions.includes(region)).length;
    if (!count) return;
    const row = document.createElement('div');
    row.className = 'filter-row';
    row.dataset.filterType  = 'region';
    row.dataset.filterValue = region;
    row.innerHTML = `
      <span class="filter-icon">${{REGION_ICONS[region] || '🌐'}}</span>
      <span class="filter-label">${{region}}</span>
      <span class="filter-count" id="count-region-${{region.replace(/ /g,'_')}}">${{count}}</span>`;
    row.addEventListener('click', () => setFilter('region', region));
    regionDiv.appendChild(row);
  }});

  // Themes
  const themeDiv = document.getElementById('theme-filters');
  themeDiv.innerHTML = '';
  ALL_THEMES.forEach(theme => {{
    const count = ARTICLES.filter(a => a.themes.includes(theme)).length;
    if (!count) return;
    const row = document.createElement('div');
    row.className = 'filter-row';
    row.dataset.filterType  = 'theme';
    row.dataset.filterValue = theme;
    row.innerHTML = `
      <span class="filter-icon">${{THEME_ICONS[theme] || '📌'}}</span>
      <span class="filter-label">${{theme}}</span>
      <span class="filter-count" id="count-theme-${{theme.replace(/ &/g,'').replace(/ /g,'_')}}">${{count}}</span>`;
    row.addEventListener('click', () => setFilter('theme', theme));
    themeDiv.appendChild(row);
  }});

  // All count
  document.getElementById('count-all').textContent = ARTICLES.length;
}}

function setFilter(type, value) {{
  activeFilter = {{ type, value }};
  document.querySelectorAll('.filter-row').forEach(r => {{
    r.classList.toggle('active',
      r.dataset.filterType === type && r.dataset.filterValue === value
    );
  }});
  render();
}}

// ── Card rendering ─────────────────────────────────────────────────────────

function makeCard(art) {{
  const pub     = PUB_META[art.pub];
  const regions = art.regions.slice(0, 3);
  const themes  = art.themes.slice(0, 3);
  const countries = art.country_names.slice(0, 4);

  const regionTags  = regions.map(r =>
    `<span class="tag tag-region" onclick="setFilter('region','${{r}}')">${{r}}</span>`
  ).join('');
  const themeTags   = themes.map(t =>
    `<span class="tag tag-theme" onclick="setFilter('theme','${{t}}')">${{THEME_ICONS[t]||''}} ${{t}}</span>`
  ).join('');
  const countryTags = countries.map(c =>
    `<span class="tag tag-country">${{c}}</span>`
  ).join('');

  return `<div class="card">
    <div class="card-header">
      <span class="pub-badge" style="background:${{pub.accent}}22;color:${{pub.accent}};border:1px solid ${{pub.accent}}44">
        ${{pub.short}}
      </span>
      <span class="card-date">${{fmtDate(art.date)}}</span>
    </div>
    <div class="card-title">
      <a href="${{art.url}}" target="_blank" rel="noopener">${{art.title}}</a>
    </div>
    ${{art.brief ? `<div class="card-brief">${{art.brief}}</div>` : ''}}
    <div class="card-tags">
      ${{regionTags}}${{themeTags}}${{countryTags}}
    </div>
    <div class="card-footer">
      <a class="read-link" href="${{art.url}}" target="_blank" rel="noopener">Read full article →</a>
    </div>
  </div>`;
}}

// ── Main render ────────────────────────────────────────────────────────────

function render() {{
  const filtered   = filterArticles(ARTICLES);
  const content    = document.getElementById('content');
  const titleEl    = document.getElementById('section-title');
  const countEl    = document.getElementById('section-count');
  const pillEl     = document.getElementById('article-count-pill');

  pillEl.innerHTML = `<b>${{filtered.length}}</b> article${{filtered.length !== 1 ? 's' : ''}}`;
  countEl.textContent = `${{filtered.length}} article${{filtered.length !== 1 ? 's' : ''}}`;

  // Title
  if (activeFilter.type === 'all') {{
    titleEl.textContent = 'All Briefings';
  }} else if (activeFilter.type === 'pub') {{
    titleEl.textContent = PUB_META[activeFilter.value].label;
  }} else {{
    titleEl.textContent = (REGION_ICONS[activeFilter.value] || THEME_ICONS[activeFilter.value] || '') + ' ' + activeFilter.value;
  }}

  if (!filtered.length) {{
    content.innerHTML = `<div class="no-results">
      <div class="no-results-icon">📭</div>
      <div>No articles match your filters.</div>
    </div>`;
    return;
  }}

  // Group by region when showing all; flat list otherwise
  const groupByRegion = (activeFilter.type === 'all' || activeFilter.type === 'pub') && !searchQuery;

  if (groupByRegion) {{
    // Build region groups
    const regionMap = new Map();
    filtered.forEach(art => {{
      const rs = art.regions.length ? art.regions : ['Uncategorized'];
      // Primary region = first detected (alphabetically first in our ordered list)
      const primary = ALL_REGIONS.find(r => rs.includes(r)) || rs[0];
      if (!regionMap.has(primary)) regionMap.set(primary, []);
      regionMap.get(primary).push(art);
    }});

    // Render groups in region order
    let html = '';
    const orderedRegions = [...ALL_REGIONS, 'Uncategorized'].filter(r => regionMap.has(r));

    orderedRegions.forEach(region => {{
      const arts    = regionMap.get(region);
      const icon    = REGION_ICONS[region] || '🌐';
      const cid     = 'rg-' + region.replace(/[^a-z0-9]/gi, '_');
      const collapsed = collapsedRegions.has(region) ? 'collapsed' : '';

      html += `<div class="region-group ${{collapsed}}" id="${{cid}}">
        <div class="region-header" onclick="toggleRegion('${{region}}')">
          <span class="region-header-icon">${{icon}}</span>
          <span class="region-header-name">${{region}}</span>
          <span class="region-header-count">${{arts.length}}</span>
          <span class="region-header-toggle">▾</span>
        </div>
        <div class="cards-grid">
          ${{arts.map(makeCard).join('')}}
        </div>
      </div>`;
    }});

    content.innerHTML = html;
  }} else {{
    content.innerHTML = `<div class="cards-grid">${{filtered.map(makeCard).join('')}}</div>`;
  }}
}}

function toggleRegion(region) {{
  if (collapsedRegions.has(region)) {{
    collapsedRegions.delete(region);
  }} else {{
    collapsedRegions.add(region);
  }}
  const cid = 'rg-' + region.replace(/[^a-z0-9]/gi, '_');
  const el = document.getElementById(cid);
  if (el) el.classList.toggle('collapsed', collapsedRegions.has(region));
}}

// ── Search ─────────────────────────────────────────────────────────────────

document.getElementById('search').addEventListener('input', e => {{
  searchQuery = e.target.value.trim();
  render();
}});

// ── Init ───────────────────────────────────────────────────────────────────

buildSidebar();
setFilter('all', 'all');
</script>
</body>
</html>"""


# ── Server + auto-open ────────────────────────────────────────────────────────

def serve_and_open(path: str, port: int) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    filename  = os.path.basename(path)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)
        def log_message(self, fmt, *args):
            pass

    with socketserver.TCPServer(("", port), Handler) as httpd:
        url = f"http://localhost:{port}/{filename}"
        print(f"\nServing at {url}")
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        httpd.serve_forever()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  Jamestown Foundation — Executive Briefing Dashboard")
    print("=" * 60)
    print(f"  Window: last {DAYS} days")
    print(f"  Output: {OUTPUT_FILE}")
    print()

    articles = fetch_all_articles(DAYS)
    print("\nClassifying articles…")
    classified = [classify_article(a) for a in articles]

    # Strip heavy full_text before JSON embedding
    for art in classified:
        art.pop("full_text", None)
        art.pop("ts", None)

    print(f"Building HTML ({len(classified)} articles)…")
    html = build_html(classified, DAYS)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved → {OUTPUT_FILE}")

    serve_and_open(OUTPUT_FILE, PORT)


if __name__ == "__main__":
    main()

