#!/usr/bin/env python3
"""
Jamestown Foundation — Multi-Publication Geopolitical Heatmap
Fetches RSS feeds and visualizes article coverage by country
over the last 90 days as a fully interactive choropleth world map.

Publications:
  • Eurasia Daily Monitor  (blue)
  • China Brief (+ China Brief Notes)  (red)
  • Terrorism Monitor      (yellow)

Run:   python jtgeopolmap.py
Opens: jf_heatmap.html in your default browser
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

DAYS = 90
REFRESH_HOURS = 6
PORT = 8742
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jf_heatmap.html")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

PUBLICATIONS = {
    "all": {
        "id":       "all",
        "label":    "All Publications",
        "short":    "ALL",
        "url":      "https://jamestown.org/",
        "accent":   "#16a34a",
        "colorscale": [
            [0.00, "#021a08"],
            [0.15, "#053d14"],
            [0.35, "#0a6622"],
            [0.55, "#16a34a"],
            [0.75, "#22c55e"],
            [0.90, "#86efac"],
            [1.00, "#f0fdf4"],
        ],
    },
    "edm": {
        "id":       "edm",
        "label":    "Eurasia Daily Monitor",
        "short":    "EDM",
        "feed":     "https://jamestown.org/publications/edm/feed/",
        "url":      "https://jamestown.org/publications/edm/",
        # Hex accent colour (taken from jamestown.org EDM nav/header)
        "accent":   "#2563eb",
        "colorscale": [
            [0.00, "#061d3c"],
            [0.15, "#0d3873"],
            [0.35, "#1260c6"],
            [0.55, "#258aff"],
            [0.75, "#4ab0ff"],
            [0.90, "#9dd4ff"],
            [1.00, "#eef7ff"],
        ],
    },
    "cb": {
        "id":       "cb",
        "label":    "China Brief",
        "short":    "CB",
        "feed":     "https://jamestown.org/publications/cb/feed/",
        "feeds":    [
            "https://jamestown.org/publications/cb/feed/",
            "https://jamestown.org/publications/china-brief-notes/feed/",
        ],
        "url":      "https://jamestown.org/publications/cb/",
        "accent":   "#dc2626",
        "colorscale": [
            [0.00, "#260707"],
            [0.15, "#710d0d"],
            [0.35, "#c61a1a"],
            [0.55, "#ff2a2a"],
            [0.75, "#ff6464"],
            [0.90, "#ffb8b8"],
            [1.00, "#fff5f5"],
        ],
    },
    "tm": {
        "id":       "tm",
        "label":    "Terrorism Monitor",
        "short":    "TM",
        "feed":     "https://jamestown.org/publications/tm/feed/",
        "url":      "https://jamestown.org/publications/tm/",
        "accent":   "#d97706",
        "colorscale": [
            [0.00, "#211400"],
            [0.15, "#714600"],
            [0.35, "#b67900"],
            [0.55, "#f5ac00"],
            [0.75, "#ffe040"],
            [0.90, "#ffec9c"],
            [1.00, "#fffde8"],
        ],
    },
}

# ── Country keyword → ISO-3166 alpha-3 mapping ────────────────────────────────

COUNTRY_MAP = {
    # ── Post-Soviet / Eastern Europe ─────────────────────────────────────────
    "Russia":              "RUS", "Russian":           "RUS", "Russians":      "RUS",
    "Kremlin":             "RUS", "Moscow":            "RUS", "Putin":         "RUS",
    "Soviet":              "RUS", "FSB":               "RUS", "GRU":           "RUS",
    "SVR":                 "RUS", "Shoigu":            "RUS", "Medvedev":      "RUS",

    "Ukraine":             "UKR", "Ukrainian":         "UKR", "Ukrainians":    "UKR",
    "Kyiv":                "UKR", "Zelensky":          "UKR", "Zelenskyy":     "UKR",
    "Donbas":              "UKR", "Donbass":           "UKR", "Mariupol":      "UKR",
    "Kharkiv":             "UKR", "Kherson":           "UKR", "Zaporizhzhia":  "UKR",
    "Crimea":              "UKR", "Odesa":             "UKR",

    "Belarus":             "BLR", "Belarusian":        "BLR", "Minsk":         "BLR",
    "Lukashenko":          "BLR",

    "Moldova":             "MDA", "Moldovan":          "MDA", "Chisinau":      "MDA",
    "Transnistria":        "MDA",

    # ── Caucasus ─────────────────────────────────────────────────────────────
    "Georgia":             "GEO", "Georgian":          "GEO", "Tbilisi":       "GEO",
    "Abkhazia":            "GEO", "South Ossetia":     "GEO",

    "Armenia":             "ARM", "Armenian":          "ARM", "Yerevan":       "ARM",
    "Nagorno-Karabakh":    "AZE", "Karabakh":          "AZE",
    "Azerbaijan":          "AZE", "Azerbaijani":       "AZE", "Baku":          "AZE",
    "Aliyev":              "AZE",

    # ── Central Asia ─────────────────────────────────────────────────────────
    "Kazakhstan":          "KAZ", "Kazakh":            "KAZ", "Astana":        "KAZ",
    "Tokayev":             "KAZ",
    "Uzbekistan":          "UZB", "Uzbek":             "UZB", "Tashkent":      "UZB",
    "Kyrgyzstan":          "KGZ", "Kyrgyz":            "KGZ", "Bishkek":       "KGZ",
    "Tajikistan":          "TJK", "Tajik":             "TJK", "Dushanbe":      "TJK",
    "Turkmenistan":        "TKM", "Turkmen":           "TKM", "Ashgabat":      "TKM",
    "Mongolia":            "MNG", "Mongolian":         "MNG", "Ulaanbaatar":   "MNG",

    # ── Eastern / Central Europe ─────────────────────────────────────────────
    "Poland":              "POL", "Polish":            "POL", "Warsaw":        "POL",
    "Hungary":             "HUN", "Hungarian":         "HUN", "Budapest":      "HUN",
    "Orbán":               "HUN", "Fidesz":            "HUN",
    "Romania":             "ROU", "Romanian":          "ROU", "Bucharest":     "ROU",
    "Bulgaria":            "BGR", "Bulgarian":         "BGR", "Sofia":         "BGR",
    "Serbia":              "SRB", "Serbian":           "SRB", "Belgrade":      "SRB",
    "Kosovo":              "RKS", "Kosovar":           "RKS",
    "Bosnia":              "BIH", "Bosnian":           "BIH", "Sarajevo":      "BIH",
    "Croatia":             "HRV", "Croatian":          "HRV", "Zagreb":        "HRV",
    "Slovenia":            "SVN", "Slovenian":         "SVN",
    "Albania":             "ALB", "Albanian":          "ALB", "Tirana":        "ALB",
    "North Macedonia":     "MKD", "Macedonian":        "MKD", "Skopje":        "MKD",
    "Montenegro":          "MNE",
    "Slovakia":            "SVK", "Slovak":            "SVK", "Bratislava":    "SVK",
    "Czech Republic":      "CZE", "Czech":             "CZE", "Prague":        "CZE",
    "Czechia":             "CZE",
    "Lithuania":           "LTU", "Lithuanian":        "LTU", "Vilnius":       "LTU",
    "Latvia":              "LVA", "Latvian":           "LVA", "Riga":          "LVA",
    "Estonia":             "EST", "Estonian":          "EST", "Tallinn":       "EST",

    # ── Western / Northern Europe ─────────────────────────────────────────────
    "Germany":             "DEU", "German":            "DEU", "Berlin":        "DEU",
    "France":              "FRA", "French":            "FRA", "Paris":         "FRA",
    "Macron":              "FRA",
    "United Kingdom":      "GBR", "Britain":           "GBR", "British":       "GBR",
    "London":              "GBR",
    "Italy":               "ITA", "Italian":           "ITA", "Rome":          "ITA",
    "Meloni":              "ITA",
    "Spain":               "ESP", "Spanish":           "ESP", "Madrid":        "ESP",
    "Netherlands":         "NLD", "Dutch":             "NLD", "Amsterdam":     "NLD",
    "Belgium":             "BEL", "Belgian":           "BEL", "Brussels":      "BEL",
    "Austria":             "AUT", "Austrian":          "AUT", "Vienna":        "AUT",
    "Sweden":              "SWE", "Swedish":           "SWE", "Stockholm":     "SWE",
    "Finland":             "FIN", "Finnish":           "FIN", "Helsinki":      "FIN",
    "Denmark":             "DNK", "Danish":            "DNK", "Copenhagen":    "DNK",
    "Norway":              "NOR", "Norwegian":         "NOR", "Oslo":          "NOR",
    "Iceland":             "ISL",
    "Switzerland":         "CHE", "Swiss":             "CHE", "Bern":          "CHE",
    "Portugal":            "PRT", "Portuguese":        "PRT", "Lisbon":        "PRT",
    "Greece":              "GRC", "Greek":             "GRC", "Athens":        "GRC",
    "Turkey":              "TUR", "Turkish":           "TUR", "Ankara":        "TUR",
    "Erdoğan":             "TUR", "Erdogan":           "TUR", "Türkiye":       "TUR",

    # ── Middle East ───────────────────────────────────────────────────────────
    "Iran":                "IRN", "Iranian":           "IRN", "Tehran":        "IRN",
    "Khamenei":            "IRN", "IRGC":              "IRN",
    "Iraq":                "IRQ", "Iraqi":             "IRQ", "Baghdad":       "IRQ",
    "Syria":               "SYR", "Syrian":            "SYR", "Damascus":      "SYR",
    "Assad":               "SYR",
    "Israel":              "ISR", "Israeli":           "ISR", "Tel Aviv":      "ISR",
    "Netanyahu":           "ISR", "IDF":               "ISR",
    "Palestine":           "PSE", "Palestinian":       "PSE", "Gaza":          "PSE",
    "Hamas":               "PSE", "West Bank":         "PSE",
    "Lebanon":             "LBN", "Lebanese":          "LBN", "Beirut":        "LBN",
    "Hezbollah":           "LBN",
    "Jordan":              "JOR", "Jordanian":         "JOR", "Amman":         "JOR",
    "Saudi Arabia":        "SAU", "Saudi":             "SAU", "Riyadh":        "SAU",
    "UAE":                 "ARE", "Emirates":          "ARE", "Dubai":         "ARE",
    "Abu Dhabi":           "ARE",
    "Qatar":               "QAT", "Qatari":            "QAT", "Doha":          "QAT",
    "Kuwait":              "KWT",
    "Bahrain":             "BHR",
    "Oman":                "OMN",
    "Yemen":               "YEM", "Yemeni":            "YEM", "Houthi":        "YEM",
    "Libya":               "LBY", "Libyan":            "LBY", "Tripoli":       "LBY",
    "Egypt":               "EGY", "Egyptian":          "EGY", "Cairo":         "EGY",
    "Tunisia":             "TUN", "Tunisian":          "TUN", "Tunis":         "TUN",
    "Algeria":             "DZA", "Algerian":          "DZA", "Algiers":       "DZA",
    "Morocco":             "MAR", "Moroccan":          "MAR", "Rabat":         "MAR",
    "Sudan":               "SDN", "Sudanese":          "SDN", "Khartoum":      "SDN",

    # ── Africa ────────────────────────────────────────────────────────────────
    "Nigeria":             "NGA", "Nigerian":          "NGA", "Abuja":         "NGA",
    "Ethiopia":            "ETH", "Ethiopian":         "ETH", "Addis Ababa":   "ETH",
    "Kenya":               "KEN", "Kenyan":            "KEN", "Nairobi":       "KEN",
    "Somalia":             "SOM", "Somali":            "SOM", "Mogadishu":     "SOM",
    "al-Shabaab":          "SOM", "Al-Shabaab":        "SOM",
    "Mali":                "MLI", "Malian":            "MLI", "Bamako":        "MLI",
    "Sahel":               "MLI",
    "Niger":               "NER",
    "Burkina Faso":        "BFA",
    "Chad":                "TCD", "Chadian":           "TCD",
    "Cameroon":            "CMR",
    "Central African Republic": "CAF", "CAR":          "CAF",
    "DR Congo":            "COD", "DRC":               "COD", "Kinshasa":      "COD",
    "Congo":               "COG",
    "Senegal":             "SEN",
    "Guinea":              "GIN",
    "South Africa":        "ZAF",
    "Mozambique":          "MOZ",
    "Zimbabwe":            "ZWE",
    "Angola":              "AGO",
    "Tanzania":            "TZA",
    "Uganda":              "UGA",
    "Rwanda":              "RWA",
    "Eritrea":             "ERI",
    "Djibouti":            "DJI",
    "Gambia":              "GMB",
    "Togo":                "TGO",
    "Benin":               "BEN",
    "Ghana":               "GHA",
    "Ivory Coast":         "CIV",

    # ── South / Southeast Asia ────────────────────────────────────────────────
    "Afghanistan":         "AFG", "Afghan":            "AFG", "Taliban":       "AFG",
    "Kabul":               "AFG",
    "Pakistan":            "PAK", "Pakistani":         "PAK", "Islamabad":     "PAK",
    "India":               "IND", "Indian":            "IND", "New Delhi":     "IND",
    "Modi":                "IND",
    "Bangladesh":          "BGD", "Bangladeshi":       "BGD", "Dhaka":         "BGD",
    "Sri Lanka":           "LKA",
    "Nepal":               "NPL", "Nepalese":          "NPL", "Kathmandu":     "NPL",
    "Myanmar":             "MMR", "Burma":             "MMR", "Burmese":       "MMR",
    "Thailand":            "THA", "Thai":              "THA", "Bangkok":       "THA",
    "Vietnam":             "VNM", "Vietnamese":        "VNM", "Hanoi":         "VNM",
    "Indonesia":           "IDN", "Indonesian":        "IDN", "Jakarta":       "IDN",
    "Philippines":         "PHL", "Filipino":          "PHL", "Manila":        "PHL",
    "Malaysia":            "MYS", "Malaysian":         "MYS", "Kuala Lumpur":  "MYS",
    "Singapore":           "SGP",
    "Cambodia":            "KHM",
    "Laos":                "LAO",

    # ── East Asia ─────────────────────────────────────────────────────────────
    "China":               "CHN", "Chinese":           "CHN", "Beijing":       "CHN",
    "CCP":                 "CHN", "Xi Jinping":        "CHN", "PRC":           "CHN",
    "PLA":                 "CHN", "Xinjiang":          "CHN", "Tibet":         "CHN",
    "Hong Kong":           "CHN", "Huawei":            "CHN",
    "Taiwan":              "TWN", "Taiwanese":         "TWN", "Taipei":        "TWN",
    "North Korea":         "PRK", "DPRK":              "PRK", "Pyongyang":     "PRK",
    "Kim Jong":            "PRK",
    "South Korea":         "KOR", "Seoul":             "KOR",
    "Japan":               "JPN", "Japanese":          "JPN", "Tokyo":         "JPN",

    # ── Americas ──────────────────────────────────────────────────────────────
    "United States":       "USA", "American":          "USA", "Washington":    "USA",
    "Pentagon":            "USA", "Biden":             "USA", "Trump":         "USA",
    "Congress":            "USA",
    "Canada":              "CAN", "Canadian":          "CAN", "Ottawa":        "CAN",
    "Mexico":              "MEX", "Mexican":           "MEX", "Mexico City":   "MEX",
    "Venezuela":           "VEN", "Venezuelan":        "VEN", "Maduro":        "VEN",
    "Cuba":                "CUB", "Cuban":             "CUB", "Havana":        "CUB",
    "Nicaragua":           "NIC", "Nicaraguan":        "NIC", "Ortega":        "NIC",
    "Colombia":            "COL", "Colombian":         "COL",
    "Brazil":              "BRA", "Brazilian":         "BRA",
    "Argentina":           "ARG", "Argentine":         "ARG",
    "Chile":               "CHL",
    "Peru":                "PER",
    "Panama":              "PAN",

    # ── Other ─────────────────────────────────────────────────────────────────
    "Australia":           "AUS", "Australian":        "AUS", "Canberra":      "AUS",
    "New Zealand":         "NZL",
    "Greenland":           "GRL",
    "Islamic State":       "IRQ", "ISIS":              "IRQ", "ISIL":          "IRQ",
    "al-Qaeda":            "AFG", "Al-Qaeda":          "AFG",
}

SORTED_KEYWORDS = sorted(COUNTRY_MAP.keys(), key=len, reverse=True)

DISPLAY_NAMES = {
    "RUS": "Russia", "UKR": "Ukraine", "BLR": "Belarus", "MDA": "Moldova",
    "GEO": "Georgia", "ARM": "Armenia", "AZE": "Azerbaijan",
    "KAZ": "Kazakhstan", "UZB": "Uzbekistan", "KGZ": "Kyrgyzstan",
    "TJK": "Tajikistan", "TKM": "Turkmenistan", "MNG": "Mongolia",
    "POL": "Poland", "HUN": "Hungary", "ROU": "Romania", "BGR": "Bulgaria",
    "SRB": "Serbia", "RKS": "Kosovo", "BIH": "Bosnia & Herzegovina",
    "HRV": "Croatia", "SVN": "Slovenia", "ALB": "Albania", "MKD": "North Macedonia",
    "MNE": "Montenegro", "SVK": "Slovakia", "CZE": "Czech Republic",
    "LTU": "Lithuania", "LVA": "Latvia", "EST": "Estonia",
    "DEU": "Germany", "FRA": "France", "GBR": "United Kingdom",
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
    "CMR": "Cameroon", "CAF": "Central African Republic",
    "COD": "DR Congo", "COG": "Congo", "SEN": "Senegal", "GIN": "Guinea",
    "ZAF": "South Africa", "MOZ": "Mozambique", "ZWE": "Zimbabwe",
    "AGO": "Angola", "TZA": "Tanzania", "UGA": "Uganda", "RWA": "Rwanda",
    "ERI": "Eritrea", "DJI": "Djibouti", "GMB": "Gambia", "TGO": "Togo",
    "BEN": "Benin", "GHA": "Ghana", "CIV": "Ivory Coast",
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

# Approximate [lon, lat] centroids for globe “face this country” animation (ISO-3166 alpha-3)
ISO3_CENTROIDS = {
    "RUS": [100.0, 61.5], "UKR": [31.2, 48.4], "BLR": [28.0, 53.5], "MDA": [28.8, 47.0],
    "GEO": [43.5, 42.2], "ARM": [44.5, 40.2], "AZE": [47.5, 40.1], "KAZ": [66.9, 48.0],
    "UZB": [63.9, 41.3], "KGZ": [74.8, 41.2], "TJK": [71.3, 38.9], "TKM": [59.6, 39.0],
    "MNG": [103.8, 46.9], "POL": [19.4, 52.1], "HUN": [19.5, 47.2], "ROU": [25.0, 46.0],
    "BGR": [25.5, 42.7], "SRB": [20.9, 44.0], "RKS": [20.9, 42.6], "BIH": [17.8, 44.2],
    "HRV": [15.2, 45.1], "SVN": [14.8, 46.1], "ALB": [20.2, 41.1], "MKD": [21.7, 41.6],
    "MNE": [19.3, 42.8], "SVK": [19.7, 48.7], "CZE": [15.3, 49.7], "LTU": [23.9, 55.2],
    "LVA": [24.6, 56.9], "EST": [25.5, 58.6], "DEU": [10.5, 51.2], "FRA": [2.2, 46.2],
    "GBR": [-3.4, 55.4], "ITA": [12.6, 42.6], "ESP": [-3.7, 40.4], "NLD": [5.3, 52.1],
    "BEL": [4.5, 50.5], "AUT": [14.6, 47.7], "SWE": [18.6, 60.1], "FIN": [26.0, 64.0],
    "DNK": [9.5, 56.2], "NOR": [8.5, 61.4], "ISL": [-18.1, 64.9], "CHE": [8.2, 46.8],
    "PRT": [-8.2, 39.6], "GRC": [21.8, 39.1], "TUR": [35.2, 39.0], "IRN": [53.7, 32.4],
    "IRQ": [44.4, 33.2], "SYR": [38.0, 35.0], "ISR": [34.9, 31.0], "PSE": [35.2, 31.9],
    "LBN": [35.9, 33.9], "JOR": [36.2, 31.0], "SAU": [45.1, 23.9], "ARE": [53.8, 23.4],
    "QAT": [51.2, 25.3], "KWT": [47.5, 29.3], "BHR": [50.6, 26.1], "OMN": [56.1, 21.0],
    "YEM": [44.2, 15.6], "LBY": [17.2, 26.3], "EGY": [30.8, 26.8], "TUN": [9.5, 34.0],
    "DZA": [2.6, 28.0], "MAR": [-7.1, 31.8], "SDN": [30.2, 15.5], "NGA": [8.7, 9.1],
    "ETH": [39.6, 9.1], "KEN": [37.9, -0.0], "SOM": [46.2, 5.2], "MLI": [-3.5, 17.6],
    "NER": [8.1, 17.6], "BFA": [-1.6, 12.2], "TCD": [18.7, 15.5], "CMR": [12.7, 6.0],
    "CAF": [20.9, 6.6], "COD": [23.7, -2.9], "COG": [15.8, -0.8], "SEN": [-14.5, 14.5],
    "GIN": [-10.7, 10.4], "ZAF": [25.0, -29.0], "MOZ": [35.5, -18.7], "ZWE": [29.2, -19.0],
    "AGO": [17.9, -11.2], "TZA": [34.9, -6.4], "UGA": [32.3, 1.4], "RWA": [29.9, -1.9],
    "ERI": [39.0, 15.0], "DJI": [42.6, 11.8], "GMB": [-15.4, 13.4], "TGO": [0.8, 8.6],
    "BEN": [2.4, 9.3], "GHA": [-1.0, 7.9], "CIV": [-5.5, 7.5], "AFG": [67.7, 33.9],
    "PAK": [69.3, 30.4], "IND": [78.9, 20.6], "BGD": [90.4, 23.7], "LKA": [80.8, 7.9],
    "NPL": [84.1, 28.4], "MMR": [95.9, 21.9], "THA": [100.6, 15.9], "VNM": [108.3, 14.1],
    "IDN": [113.9, -0.8], "PHL": [122.6, 11.8], "MYS": [101.7, 3.2], "SGP": [103.8, 1.4],
    "KHM": [104.9, 12.6], "LAO": [102.5, 19.9], "CHN": [104.2, 35.9], "TWN": [121.0, 23.7],
    "PRK": [127.8, 40.0], "KOR": [127.8, 36.5], "JPN": [138.3, 36.2], "USA": [-98.35, 39.5],
    "CAN": [-106.3, 56.1], "MEX": [-102.6, 23.6], "VEN": [-66.6, 6.4], "CUB": [-79.0, 21.5],
    "NIC": [-85.0, 12.9], "COL": [-74.3, 4.6], "BRA": [-51.9, -14.2], "ARG": [-63.6, -38.4],
    "CHL": [-71.5, -35.7], "PER": [-75.0, -9.2], "PAN": [-80.8, 8.5], "AUS": [134.0, -25.0],
    "NZL": [172.0, -43.5], "GRL": [-40.0, 72.0],
}

# Natural Earth / world-atlas 110m country feature `id` → ISO-3166 alpha-3 (UN M.49 numeric as string keys)
_NE_ISO_NUMERIC = {
    "RUS": "643", "UKR": "804", "BLR": "112", "MDA": "498", "GEO": "268", "ARM": "051",
    "AZE": "031", "KAZ": "398", "UZB": "860", "KGZ": "417", "TJK": "762", "TKM": "795",
    "MNG": "496", "POL": "616", "HUN": "348", "ROU": "642", "BGR": "100", "SRB": "688",
    "RKS": "383", "BIH": "070", "HRV": "191", "SVN": "705", "ALB": "008", "MKD": "807",
    "MNE": "499", "SVK": "703", "CZE": "203", "LTU": "440", "LVA": "428", "EST": "233",
    "DEU": "276", "FRA": "250", "GBR": "826", "ITA": "380", "ESP": "724", "NLD": "528",
    "BEL": "056", "AUT": "040", "SWE": "752", "FIN": "246", "DNK": "208", "NOR": "578",
    "ISL": "352", "CHE": "756", "PRT": "620", "GRC": "300", "TUR": "792", "IRN": "364",
    "IRQ": "368", "SYR": "760", "ISR": "376", "PSE": "275", "LBN": "422", "JOR": "400",
    "SAU": "682", "ARE": "784", "QAT": "634", "KWT": "414", "BHR": "048", "OMN": "512",
    "YEM": "887", "LBY": "434", "EGY": "818", "TUN": "788", "DZA": "012", "MAR": "504",
    "SDN": "729", "NGA": "566", "ETH": "231", "KEN": "404", "SOM": "706", "MLI": "466",
    "NER": "562", "BFA": "854", "TCD": "148", "CMR": "120", "CAF": "140", "COD": "180",
    "COG": "178", "SEN": "686", "GIN": "324", "ZAF": "710", "MOZ": "508", "ZWE": "716",
    "AGO": "024", "TZA": "834", "UGA": "800", "RWA": "646", "ERI": "232", "DJI": "262",
    "GMB": "270", "TGO": "768", "BEN": "204", "GHA": "288", "CIV": "384", "AFG": "004",
    "PAK": "586", "IND": "356", "BGD": "050", "LKA": "144", "NPL": "524", "MMR": "104",
    "THA": "764", "VNM": "704", "IDN": "360", "PHL": "608", "MYS": "458", "SGP": "702",
    "KHM": "116", "LAO": "418", "CHN": "156", "TWN": "158", "PRK": "408", "KOR": "410",
    "JPN": "392", "USA": "840", "CAN": "124", "MEX": "484", "VEN": "862", "CUB": "192",
    "NIC": "558", "COL": "170", "BRA": "076", "ARG": "032", "CHL": "152", "PER": "604",
    "PAN": "591", "AUS": "036", "NZL": "554", "GRL": "304",
}
NE_NUM_TO_ISO3 = {num: iso for iso, num in _NE_ISO_NUMERIC.items()}


# ── RSS fetching ──────────────────────────────────────────────────────────────

def fetch_articles(pub_id: str, feed_url: str, days: int = 90) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    articles = []
    page = 1

    session = requests.Session()
    session.headers.update(HEADERS)

    while page <= 50:
        url = f"{feed_url}?paged={page}"
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
                    print(f"    Warning: page {page} failed ({exc}), stopping.")
        if resp is None or not resp.ok:
            break

        feed = feedparser.parse(resp.text)
        if not feed.entries:
            break

        reached_cutoff = False
        for entry in feed.entries:
            if not getattr(entry, "published_parsed", None):
                continue
            pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            if pub < cutoff:
                reached_cutoff = True
                break

            content_html = ""
            if hasattr(entry, "content") and entry.content:
                content_html = entry.content[0].value
            elif hasattr(entry, "summary"):
                content_html = entry.summary

            text = BeautifulSoup(content_html, "html.parser").get_text(" ")

            articles.append({
                "title":   entry.title.strip(),
                "date":    pub.strftime("%Y-%m-%d"),
                "url":     entry.link,
                "summary": text[:500].strip(),
                "text":    (entry.title + " " + text),
            })

        print(f"    Page {page}: {len(articles)} articles so far")
        if reached_cutoff:
            break
        page += 1

    return articles


# ── Geographic extraction ─────────────────────────────────────────────────────

def extract_countries(text: str) -> set[str]:
    found = set()
    for keyword in SORTED_KEYWORDS:
        pattern = r"\b" + re.escape(keyword) + r"\b"
        if re.search(pattern, text, re.IGNORECASE):
            found.add(COUNTRY_MAP[keyword])
    return found


def build_country_data(articles: list[dict]) -> dict:
    data: dict[str, dict] = defaultdict(lambda: {"count": 0, "articles": []})
    for art in articles:
        codes = extract_countries(art["text"])
        codes_sorted = sorted(codes)          # stable list for the front end
        for code in codes:
            data[code]["count"] += 1
        country_weights = {c: data[c]["count"] for c in codes_sorted}
        for code in codes:
            data[code]["articles"].append({
                "title":     art["title"],
                "date":      art["date"],
                "url":       art["url"],
                "summary":   art["summary"],
                "countries": codes_sorted,    # all ISO-3 codes this article mentions
                "country_weights": country_weights,
            })
    for code in data:
        data[code]["articles"].sort(key=lambda x: x["date"], reverse=True)
    return dict(data)


def build_chart_payload(country_data: dict) -> dict:
    iso3_list, count_list, hover_list, name_list = [], [], [], []
    for iso3, info in sorted(country_data.items(), key=lambda x: -x[1]["count"]):
        display = DISPLAY_NAMES.get(iso3, iso3)
        count = info["count"]
        arts = info["articles"][:8]
        hover_lines = [f"<b>{display}</b>", f"Articles: {count}", ""]
        for a in arts:
            title = a["title"][:72] + ("…" if len(a["title"]) > 72 else "")
            hover_lines.append(f"{a['date']} — {title}")
        iso3_list.append(iso3)
        count_list.append(count)
        hover_list.append("<br>".join(hover_lines))
        name_list.append(display)
    return {"iso3": iso3_list, "count": count_list, "hover": hover_list, "name": name_list}


def build_sidebar_payload(country_data: dict) -> dict:
    out = {}
    for iso3, info in country_data.items():
        out[iso3] = {
            "name":     DISPLAY_NAMES.get(iso3, iso3),
            "count":    info["count"],
            "articles": info["articles"],
        }
    return out


# ── HTML generation ───────────────────────────────────────────────────────────

def build_html(all_data: dict, days: int) -> str:
    """Build a single self-contained HTML file with all three datasets."""

    pub_meta_js = {}
    for pid, pub in PUBLICATIONS.items():
        pub_meta_js[pid] = {
            "label":      pub["label"],
            "short":      pub["short"],
            "accent":     pub["accent"],
            "colorscale": pub["colorscale"],
            "url":        pub["url"],
        }

    datasets_js = {}
    for pid, info in all_data.items():
        articles = info["articles"]
        date_range = ""
        if articles:
            oldest = min(a["date"] for a in articles)
            newest = max(a["date"] for a in articles)
            date_range = f"{oldest} → {newest}"
        recent = sorted(articles, key=lambda a: a["date"], reverse=True)[:40]
        cd = info["country_data"]
        recent_payload = []
        for a in recent:
            codes_sorted = sorted(extract_countries(a["text"]))
            recent_payload.append({
                "title":   a["title"],
                "date":    a["date"],
                "url":     a["url"],
                "countries": codes_sorted,
                "country_weights": {
                    code: cd[code]["count"]
                    for code in codes_sorted
                    if code in cd
                },
            })
        datasets_js[pid] = {
            "chart":   build_chart_payload(info["country_data"]),
            "sidebar": build_sidebar_payload(info["country_data"]),
            "total":   len(articles),
            "range":   date_range,
            "countries": len(info["country_data"]),
            "recent":  recent_payload,
        }

    pub_meta_json = json.dumps(pub_meta_js)
    datasets_json = json.dumps(datasets_js)
    iso3_centroids_json = json.dumps(ISO3_CENTROIDS)
    ne_num_to_iso3_json = json.dumps(NE_NUM_TO_ISO3)

    _globe_embed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "globe_d3_embed.js")
    with open(_globe_embed_path, encoding="utf-8") as _globe_f:
        globe_d3_js = _globe_f.read()

    return (
        f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jamestown Foundation — Geopolitical Heatmap</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Literata:ital,opsz,wght@0,7..72,300..700;1,7..72,300..500&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/topojson-client@3/dist/topojson-client.min.js"></script>
<style>
  @font-face {{
    font-family: 'Mazius Display';
    src: local('Mazius Display'), local('MaziusDisplay'), local('Mazius-Display');
    font-weight: normal;
    font-style: normal;
  }}
  @font-face {{
    font-family: 'Mazius Display';
    src: local('Mazius Display Italic'), local('MaziusDisplay-Italic'), local('Mazius Display ExtraItalic');
    font-weight: normal;
    font-style: italic;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Literata', Georgia, 'Times New Roman', serif;
    background: #0d1117;
    color: #e6edf3;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }}

  /* ── Header ── */
  header {{
    background: #161b22;
    border-bottom: 1px solid #30363d;
    flex-shrink: 0;
  }}
  .header-top {{
    padding: 10px 20px 0;
    display: flex;
    align-items: center;
    gap: 12px;
  }}
  .header-top h1 {{
    font-family: 'Mazius Display', 'Palatino Linotype', Palatino, 'Book Antiqua', serif;
    font-size: 1.15rem;
    font-weight: normal;
    color: #f0f6fc;
    white-space: nowrap;
    letter-spacing: 0.02em;
  }}
  .header-top .divider {{ color: #30363d; }}
  .header-meta {{
    font-size: 0.75rem;
    color: #8b949e;
    white-space: nowrap;
  }}
  #date-range {{ color: #8b949e; }}
  .hint {{
    margin-left: auto;
    font-size: 0.7rem;
    color: #484f58;
    white-space: nowrap;
  }}

  /* ── Publication toggle tabs ── */
  .pub-tabs {{
    display: flex;
    gap: 0;
    padding: 8px 20px 0;
    align-items: flex-end;
  }}
  .proj-toggle {{
    margin-left: auto;
    display: flex;
    gap: 0;
    align-items: flex-end;
    padding-bottom: 0;
  }}
  .proj-btn {{
    padding: 7px 18px;
    font-family: 'Mazius Display', 'Palatino Linotype', Palatino, serif;
    font-size: 0.78rem;
    cursor: pointer;
    border: 1px solid #30363d;
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    background: #0d1117;
    color: #484f58;
    transition: all 0.15s;
    user-select: none;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }}
  .proj-btn:not(:first-child) {{ margin-left: 3px; }}
  .proj-btn.active {{
    color: #8b949e;
    border-color: #484f58;
    background: #161b22;
  }}
  .pub-tab {{
    padding: 9px 24px;
    font-family: 'Mazius Display', 'Palatino Linotype', Palatino, serif;
    font-size: 0.88rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid #30363d;
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    background: #0d1117;
    color: #8b949e;
    transition: all 0.15s;
    user-select: none;
    letter-spacing: 0.01em;
  }}
  .pub-tab:not(:first-child) {{ margin-left: 4px; }}
  .pub-tab:hover {{ color: #f0f6fc; background: #1c2128; }}
  .pub-tab.active {{
    background: #161b22;
    color: #f0f6fc;
    border-bottom: 2px solid var(--accent);
    margin-bottom: -1px;
    position: relative;
    z-index: 1;
  }}

  /* ── Main layout ── */
  .main {{
    position: relative;
    isolation: isolate;
    display: flex;
    flex: 1;
    overflow: hidden;
    min-height: 0;
  }}
  #map-container {{
    flex: 1;
    position: relative;
    min-width: 0;
    min-height: 0;
    isolation: isolate;
    overflow: hidden;
    z-index: 0;
  }}
  /* Absolute fill — overflow visible so colorbar SVG is not clipped at the div edge */
  #plotly-map {{
    position: absolute;
    inset: 0;
    width: 100% !important;
    height: 100% !important;
    overflow: visible;
  }}
  #plotly-map .modebar-container {{
    z-index: 10000 !important;
  }}

  #globe-container {{
    position: absolute;
    inset: 0;
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 3;
    pointer-events: auto;
  }}
  #globe-container.globe-visible {{
    display: flex;
  }}
  #globe-container svg {{
    display: block;
    max-width: 100%;
    max-height: 100%;
  }}
  .globe-crt {{
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: 5;
    background:
      repeating-linear-gradient(
        to bottom,
        transparent 0px,
        transparent 2px,
        rgba(0, 0, 0, 0.26) 2px,
        rgba(0, 0, 0, 0.26) 4px
      ),
      radial-gradient(
        ellipse at center,
        transparent 55%,
        rgba(0, 0, 0, 0.58) 100%
      );
    mix-blend-mode: multiply;
    animation: flicker 0.15s infinite;
  }}
  #globe-legend {{
    position: absolute;
    right: 16px;
    top: 50%;
    transform: translateY(-50%);
    z-index: 10000;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    pointer-events: none;
    background: rgba(22, 27, 34, 0.85);
    backdrop-filter: blur(6px);
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 8px 6px;
  }}
  #globe-legend canvas {{
    border: none !important;
    border-radius: 2px;
    background: #161b22;
    display: block;
  }}

  /* ── Sidebar ── */
  #sidebar {{
    position: relative;
    z-index: 1;
    isolation: isolate;
    width: 350px;
    background: #161b22;
    border-left: 1px solid #30363d;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    flex-shrink: 0;
    min-height: 0;
  }}

  /* ── Recent stories panel ── */
  #recent-panel {{
    flex-shrink: 0;
    height: 195px;
    overflow-y: auto;
    border-bottom: 2px solid #30363d;
    scrollbar-width: thin;
    scrollbar-color: #30363d #161b22;
  }}
  #recent-panel::-webkit-scrollbar {{ width: 4px; }}
  #recent-panel::-webkit-scrollbar-track {{ background: #161b22; }}
  #recent-panel::-webkit-scrollbar-thumb {{ background: #30363d; border-radius: 2px; }}
  #recent-panel-hdr {{
    position: sticky;
    top: 0;
    z-index: 2;
    background: #161b22;
    padding: 7px 16px 5px;
    font-size: 0.62rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: #8b949e;
    border-bottom: 1px solid #21262d;
    display: flex;
    align-items: center;
    gap: 6px;
  }}
  #recent-pub-dot {{
    width: 7px; height: 7px;
    border-radius: 50%;
    background: #8b949e;
    transition: background 0.3s;
    flex-shrink: 0;
  }}
  .recent-card {{
    padding: 5px 16px 6px;
    border-bottom: 1px solid #1a1f27;
    cursor: pointer;
    transition: background 0.1s;
    text-decoration: none;
    display: block;
  }}
  .recent-card:last-child {{ border-bottom: none; }}
  .recent-card:hover {{ background: #1c2128; }}
  .recent-card-date {{
    font-family: 'Literata', Georgia, serif;
    font-size: 0.60rem;
    color: #6e7681;
    margin-bottom: 2px;
  }}
  .recent-card-title {{
    font-family: 'Literata', Georgia, serif;
    font-size: 0.72rem;
    color: #c9d1d9;
    line-height: 1.35;
  }}
  .recent-card:hover .recent-card-title {{ color: #f0f6fc; }}

  #sidebar-header {{
    padding: 12px 16px 10px;
    border-bottom: 1px solid #30363d;
    flex-shrink: 0;
  }}
  #sidebar-country {{
    font-family: 'Mazius Display', 'Palatino Linotype', Palatino, serif;
    font-size: 1.05rem;
    font-weight: normal;
    color: #f0f6fc;
    letter-spacing: 0.02em;
  }}
  #sidebar-count {{
    font-family: 'Literata', Georgia, serif;
    font-size: 0.74rem;
    color: #8b949e;
    margin-top: 2px;
  }}
  #sidebar-articles {{
    overflow-y: auto;
    flex: 1;
    min-height: 0;
    padding: 8px 0;
  }}
  .article-card {{
    display: block;
    padding: 9px 16px;
    border-bottom: 1px solid #21262d;
    text-decoration: none;
    cursor: pointer;
    transition: background 0.1s;
  }}
  .article-card:hover {{ background: #1c2128; }}
  .article-date {{
    font-family: 'Literata', Georgia, serif;
    font-size: 0.68rem;
    color: #8b949e;
    margin-bottom: 3px;
    font-variant-numeric: tabular-nums;
  }}
  .article-title {{
    font-family: 'Mazius Display', 'Palatino Linotype', Palatino, serif;
    font-size: 0.85rem;
    font-weight: normal;
    line-height: 1.4;
    text-decoration: none;
    color: var(--accent, #58a6ff);
    letter-spacing: 0.01em;
  }}
  .article-card:hover .article-title {{ filter: brightness(1.15); }}
  .article-summary {{
    font-family: 'Literata', Georgia, serif;
    font-size: 0.71rem;
    color: #8b949e;
    margin-top: 4px;
    line-height: 1.5;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }}
  #sidebar-empty {{
    font-family: 'Literata', Georgia, serif;
    padding: 40px 20px;
    text-align: center;
    color: #484f58;
    font-size: 0.82rem;
    line-height: 1.65;
  }}
  #sidebar-empty svg {{ opacity: 0.25; margin-bottom: 12px; }}

  /* ── Stats bar ── */
  #stats-bar {{
    font-family: 'Literata', Georgia, serif;
    background: #161b22;
    border-top: 1px solid #30363d;
    padding: 5px 20px;
    font-size: 0.7rem;
    color: #484f58;
    flex-shrink: 0;
    display: flex;
    gap: 18px;
    align-items: center;
  }}
  #stats-bar b {{ color: #8b949e; }}
  .header-meta, .hint {{
    font-family: 'Literata', Georgia, serif;
  }}

  /* Scrollbar */
  #sidebar-articles::-webkit-scrollbar {{ width: 4px; }}
  #sidebar-articles::-webkit-scrollbar-track {{ background: transparent; }}
  #sidebar-articles::-webkit-scrollbar-thumb {{ background: #30363d; border-radius: 2px; }}

  /* Fuzzy film-grain overlay (low z — below Plotly SVG / CRT ::after) */
  #fuzzy-overlay {{
    position: absolute;
    inset: -100%;
    width: 300%;
    height: 300%;
    pointer-events: none;
    z-index: 1;
    opacity: 0.055;
    background-repeat: repeat;
    background-size: 180px 180px;
    animation: fuzzy-drift 0.18s linear infinite alternate;
  }}
  @keyframes fuzzy-drift {{
    from {{ transform: translateX(-10%) translateY(-10%); }}
    to   {{ transform: translateX(10%)  translateY(10%);  }}
  }}

  /* CRT scanlines + vignette (low z — below Plotly SVG layers) */
  #map-container::after {{
    content: '';
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: 2;
    background:
      repeating-linear-gradient(
        to bottom,
        transparent 0px,
        transparent 2px,
        rgba(0, 0, 0, 0.26) 2px,
        rgba(0, 0, 0, 0.26) 4px
      ),
      radial-gradient(
        ellipse at center,
        transparent 55%,
        rgba(0, 0, 0, 0.58) 100%
      );
    mix-blend-mode: multiply;
    animation: flicker 0.15s infinite;
  }}
  @keyframes flicker {{
    0%   {{ opacity: 1; }}
    88%  {{ opacity: 1; }}
    89%  {{ opacity: 0.86; }}
    90%  {{ opacity: 1; }}
    96%  {{ opacity: 1; }}
    97%  {{ opacity: 0.91; }}
    98%  {{ opacity: 1; }}
    100% {{ opacity: 1; }}
  }}

  /* Hide Plotly's SVG tooltip — we use our own HTML one */
  .hoverlayer .hovertext {{ display: none !important; }}

  /* ── Custom map tooltip ─────────────────────────────────────────────── */
  #map-tooltip {{
    position: absolute;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 9px 13px;
    font-family: 'Literata', Georgia, serif;
    font-size: 0.78rem;
    color: #f0f6fc;
    line-height: 1.55;
    pointer-events: none;
    z-index: 10001;
    max-width: 240px;
    display: none;
    white-space: normal;
  }}
  #map-tooltip b {{
    font-family: 'Mazius Display', 'Palatino Linotype', Palatino, serif;
    font-size: 0.92rem;
    display: block;
    margin-bottom: 2px;
  }}
  #map-tooltip.crimea-override {{
    border-color: #dc2626;
  }}
  #map-tooltip.visible {{ display: block; }}

  @keyframes tt-displace {{
    0%,100% {{ transform: none;                           filter: none; }}
    8%      {{ transform: translateX(-10px) skewX(-2deg); filter: hue-rotate(-35deg); }}
    14%     {{ transform: translateX(12px);               filter: hue-rotate(22deg); }}
    21%     {{ transform: translateX(-7px) skewX(2deg);   filter: hue-rotate(-12deg); }}
    28%     {{ transform: translateX(5px);                filter: none; }}
    35%     {{ transform: none; }}
  }}
  @keyframes tt-chroma {{
    0%,100% {{ box-shadow: none; }}
    8%      {{ box-shadow:  10px 0 0 rgba(255,40,40,.5), -6px 0 0 rgba(40,40,255,.5); }}
    14%     {{ box-shadow: -12px 0 0 rgba(255,40,40,.5),  8px 0 0 rgba(40,40,255,.5); }}
    21%     {{ box-shadow:   8px 0 0 rgba(255,40,40,.5), -10px 0 0 rgba(40,40,255,.5); }}
    28%     {{ box-shadow: none; }}
  }}
  #map-tooltip.glitching {{
    animation: tt-displace 0.34s steps(1, end) forwards,
               tt-chroma   0.34s steps(1, end) forwards;
  }}

</style>
</head>
<body>

<!-- ── Loading screen ─────────────────────────────────────────────────── -->
<div id="loader">
  <div id="loader-inner">
    <div id="loader-title">Jamestown</div>
    <div id="loader-sub">Foundation</div>
    <div id="loader-bar">......</div>
    <div id="loader-label">Loading coverage data</div>
    <div id="loader-phones" style="
      font-family:'Literata',Georgia,serif;
      font-size:0.68rem;color:#484f58;
      letter-spacing:0.06em;margin-top:-6px;">
      🎧 headphones recommended
    </div>
    <div id="loader-enter"></div>
  </div>
</div>

<style>
#loader {{
  position: fixed; inset: 0; z-index: 99999;
  background: #0d1117;
  display: flex; align-items: center; justify-content: center;
  transition: opacity 0.7s ease;
}}
#loader.ready-to-enter {{ cursor: pointer; }}
#loader.fade-out {{ opacity: 0; pointer-events: none; }}

#loader-inner {{
  display: flex; flex-direction: column;
  align-items: center; gap: 18px;
  user-select: none;
}}

#loader-title {{
  font-family: 'Mazius Display', 'Palatino Linotype', Palatino, serif;
  font-size: clamp(2.8rem, 6vw, 5rem);
  color: #f0f6fc;
  letter-spacing: 0.04em;
  line-height: 1;
  position: relative;
  text-shadow: 0 0 12px rgba(210,228,255,0.1);
}}

#loader-sub {{
  font-family: 'Literata', Georgia, serif;
  font-size: clamp(0.75rem, 1.4vw, 1rem);
  color: #8b949e;
  letter-spacing: 0.25em;
  text-transform: uppercase;
  margin-top: -14px;
}}

#loader-bar {{
  font-family: 'Courier New', Courier, monospace;
  font-size: clamp(1.1rem, 2.5vw, 1.6rem);
  letter-spacing: 0.18em;
  color: #f0f6fc;
  min-width: 7ch;
  text-align: center;
  transition: color 0.2s;
}}

#loader-label {{
  font-family: 'Literata', Georgia, serif;
  font-size: 0.72rem;
  color: #484f58;
  letter-spacing: 0.1em;
}}

#loader-enter {{
  font-family: 'Literata', Georgia, serif;
  font-size: 0.78rem;
  color: #8b949e;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.25s ease, color 0.15s;
  min-height: 1.4em;
}}
#loader-enter.visible {{ opacity: 1; }}
#loader-enter:hover {{ color: #f0f6fc; }}
</style>

<script>
(function() {{
  const bar    = document.getElementById('loader-bar');
  const label  = document.getElementById('loader-label');
  const enter  = document.getElementById('loader-enter');
  const loader = document.getElementById('loader');
  const total  = 6;
  bar.textContent = '|'.repeat(total);
  bar.style.color = '#2563eb';
  label.style.opacity = '0';
  enter.textContent = '[ click anywhere · or press Enter ]';
  enter.classList.add('visible');
  loader.classList.add('ready-to-enter');

  let dismissed = false;
  function dismissLoader() {{
    if (dismissed) return;
    dismissed = true;
    document.removeEventListener('keydown', onEnterKey);
    loader.removeEventListener('click', dismissLoader);
    enter.style.opacity = '0';
    loader.classList.remove('ready-to-enter');
    loader.classList.add('fade-out');
    setTimeout(() => loader.remove(), 400);
    if (typeof ensureCtx === 'function') {{ ensureCtx(); startAmbient(); }}
  }}
  function onEnterKey(e) {{
    if (dismissed) return;
    if (e.key !== 'Enter') return;
    e.preventDefault();
    dismissLoader();
  }}
  document.addEventListener('keydown', onEnterKey);
  loader.addEventListener('click', dismissLoader);
}})();
</script>

<header>
  <div class="header-top">
    <h1>Jamestown Foundation — Geopolitical Heatmap</h1>
    <span class="divider">|</span>
    <span class="header-meta">Last {days} days &nbsp;·&nbsp; <span id="date-range"></span></span>
    <span class="hint">Click any shaded country to browse articles</span>
  </div>
  <div class="pub-tabs">
    <div class="pub-tab" data-pub="all" onclick="switchPub('all')">All Coverage</div>
    <div class="pub-tab" data-pub="edm" onclick="switchPub('edm')">Eurasia Daily Monitor</div>
    <div class="pub-tab" data-pub="cb"  onclick="switchPub('cb')">China Brief</div>
    <div class="pub-tab" data-pub="tm"  onclick="switchPub('tm')">Terrorism Monitor</div>
    <div class="proj-toggle">
      <div class="proj-btn active" id="proj-flat"   onclick="setProjection('robinson')">Flat</div>
      <div class="proj-btn"        id="proj-globe"  onclick="setProjection('globe')">Globe</div>
    </div>
  </div>
</header>

<div class="main">
  <div id="map-container">
    <div id="fuzzy-overlay"></div>
    <div id="plotly-map"></div>
    <div id="globe-container" aria-hidden="true">
      <div id="globe-crt-overlay" class="globe-crt"></div>
    </div>
    <div id="globe-legend" aria-hidden="false">
      <span style="font-family:'Literata',Georgia,serif;font-size:0.60rem;color:#8b949e;margin-bottom:4px;">Articles</span>
      <span id="globe-legend-max" style="font-family:'Literata',Georgia,serif;font-size:0.60rem;color:#8b949e;">—</span>
      <canvas id="globe-legend-canvas" width="12" height="200"
        style="border:1px solid #30363d;border-radius:2px;background:#161b22;display:block;"></canvas>
      <span style="font-family:'Literata',Georgia,serif;font-size:0.60rem;color:#8b949e;">0</span>
    </div>
    <div id="map-tooltip"></div>
  </div>
  <div id="sidebar">
    <div id="recent-panel">
      <div id="recent-panel-hdr">
        <span id="recent-pub-dot"></span>
        <span id="recent-panel-label">Most Recent Stories</span>
      </div>
      <!-- populated by populateRecentPanel() -->
    </div>
    <div id="sidebar-header">
      <div id="sidebar-country">Select a country</div>
      <div id="sidebar-count"></div>
    </div>
    <div id="sidebar-articles">
      <div id="sidebar-empty">
        <svg width="32" height="32" fill="none" viewBox="0 0 24 24">
          <path stroke="currentColor" stroke-width="1.5" stroke-linecap="round"
                d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 8v4M12 16h.01"/>
        </svg>
        <div>Select a publication tab above,<br>then click a highlighted country</div>
      </div>
    </div>
  </div>
</div>

<div id="stats-bar">
  <span>Source: <b id="stat-pub">—</b></span>
  <span>Articles: <b id="stat-arts">—</b></span>
  <span>Countries covered: <b id="stat-ctry">—</b></span>
  <span>Generated: <b>{datetime.now().strftime("%Y-%m-%d %H:%M")}</b></span>
  <span style="margin-left:auto"><a id="stat-link" href="#" target="_blank"
    style="color:#484f58;text-decoration:none;font-size:0.68rem;">
    jamestown.org ↗</a></span>
  <span style="margin-left:8px;">
    <button id="credits-btn" onclick="showCredits()"
      style="background:none;border:none;padding:0;color:#484f58;
             text-decoration:none;font-size:0.68rem;cursor:pointer;
             font-family:'Literata',Georgia,serif;letter-spacing:0;">
      Credits
    </button>
  </span>
  <span style="margin-left:8px;">
    <button id="help-btn" onclick="showHelp()"
      style="background:none;border:none;padding:0;color:#484f58;
             font-size:0.68rem;cursor:pointer;
             font-family:'Literata',Georgia,serif;">
      Help
    </button>
  </span>
</div>

<script>
const PUB_META = {pub_meta_json};
const DATASETS = {datasets_json};
const ISO3_CENTROID = {iso3_centroids_json};
const NE_NUM_TO_ISO3 = {ne_num_to_iso3_json};

let currentPub = null;
let plotlyInited = false;
/** True while first Plotly.newPlot → addTraces is in flight (prevents duplicate newPlot). */
let plotlyInitializing = false;

function showCredits() {{
  const m = document.getElementById('credits-modal');
  m.style.display = 'flex';
  // close on backdrop click
  m.onclick = (e) => {{ if (e.target === m) hideCredits(); }};
  // close on Escape
  document._creditsEsc = (e) => {{ if (e.key === 'Escape') hideCredits(); }};
  document.addEventListener('keydown', document._creditsEsc);
}}
function hideCredits() {{
  document.getElementById('credits-modal').style.display = 'none';
  if (document._creditsEsc) {{
    document.removeEventListener('keydown', document._creditsEsc);
    document._creditsEsc = null;
  }}
}}
function showHelp() {{
  const m = document.getElementById('help-modal');
  m.style.display = 'flex';
  m.onclick = (e) => {{ if (e.target === m) hideHelp(); }};
  document._helpEsc = (e) => {{ if (e.key === 'Escape') hideHelp(); }};
  document.addEventListener('keydown', document._helpEsc);
}}
function hideHelp() {{
  document.getElementById('help-modal').style.display = 'none';
  if (document._helpEsc) {{
    document.removeEventListener('keydown', document._helpEsc);
    document._helpEsc = null;
  }}
}}

// ── Plotly base layout (static, never changes) ────────────────────────────
const BASE_LAYOUT = {{
  geo: {{
    domain:         {{ x: [0, 1], y: [0, 1] }},
    showframe:      false,
    framewidth:     0,
    framecolor:     'rgba(0,0,0,0)',
    showcoastlines: false,
    coastlinecolor: '#1e2530',
    showgraticules: true,
    graticulescolor: '#1e2030',
    lonaxis:        {{ showgrid: false }},
    lataxis:        {{ showgrid: false }},
    showland:       true,
    landcolor:      '#1e2535',
    showocean:      true,
    oceancolor:     '#0d1117',
    showlakes:      true,
    lakecolor:      '#0d1117',
    showcountries:  true,
    countrycolor:   '#21262d',
    bgcolor:        '#0a0e17',
    projection:     {{ type: 'robinson' }},
  }},
  paper_bgcolor: '#0d1117',
  plot_bgcolor:  '#0d1117',
  /* Right margin matches HTML legend (#globe-legend) inset from map edge */
  margin:        {{ t: 4, b: 4, l: 8, r: 16 }},
  dragmode:      'zoom',
  font: {{
    family: "'Literata', Georgia, 'Times New Roman', serif",
    color:  '#f0f6fc',
  }},
  hoverlabel: {{
    font: {{
      family: "'Literata', Georgia, 'Times New Roman', serif",
      size:   13,
      color:  '#f0f6fc',
    }},
    bgcolor:     '#161b22',
    bordercolor: '#30363d',
  }},
}};

const MAP_MARGIN = {{ t: 4, b: 4, l: 8, r: 16 }};
const GEO_DOMAIN_FULL = {{ x: [0, 1], y: [0, 1] }};

/** Crimea bbox (lon/lat) — flat map: RUS polygon includes Crimea; use pointer + projection to show override tooltip */
const CRIMEA_LON_RANGE = [32.5, 36.7];
const CRIMEA_LAT_RANGE = [44.4, 46.3];

/** Mouse position → lon/lat using Plotly geo subplot projection (same space as choropleth). */
function flatMapPointerToLonLat(gd, ev) {{
  try {{
    const geo = gd._fullLayout && gd._fullLayout.geo && gd._fullLayout.geo._subplot;
    if (!geo || !geo.projection || !geo.bgRect) return null;
    const node = geo.bgRect.node();
    const r = node.getBoundingClientRect();
    const x = ev.clientX - r.left;
    const y = ev.clientY - r.top;
    const lonlat = geo.projection.invert([x, y]);
    if (!lonlat || lonlat.length < 2) return null;
    const lon = lonlat[0];
    const lat = lonlat[1];
    if (!isFinite(lon) || !isFinite(lat)) return null;
    return {{ lon, lat }};
  }} catch (e) {{
    return null;
  }}
}}

function isLonLatInCrimeaBox(ll) {{
  if (!ll) return false;
  return (
    ll.lon >= CRIMEA_LON_RANGE[0] &&
    ll.lon <= CRIMEA_LON_RANGE[1] &&
    ll.lat >= CRIMEA_LAT_RANGE[0] &&
    ll.lat <= CRIMEA_LAT_RANGE[1]
  );
}}

const PLOTLY_CONFIG = {{
  /* Globe uses D3, not Plotly — safe to let flat map fill its container */
  responsive:    true,
  scrollZoom:    true,
  doubleClick:   'reset',
  displaylogo:   false,
  modeBarButtonsToRemove: ['select2d', 'lasso2d'],
  toImageButtonOptions: {{ format: 'png', filename: 'jf_heatmap', scale: 2 }},
}};

// ── Publication switch — audio + full-screen glitch overlay ────────────────
function playPubSwitch() {{
  if (!soundEnabled) return;
  ensureCtx();

  const now   = audioCtx.currentTime;
  const dur   = 0.32;
  const sRate = audioCtx.sampleRate;

  const len = Math.floor(sRate * dur);
  const buf = audioCtx.createBuffer(1, len, sRate);
  const d   = buf.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;

  const noiseSrc = audioCtx.createBufferSource();
  noiseSrc.buffer = buf;

  const bp  = audioCtx.createBiquadFilter();
  bp.type   = 'bandpass';
  bp.Q.value = 0.9;
  bp.frequency.setValueAtTime(1400, now);
  bp.frequency.exponentialRampToValueAtTime(180, now + dur * 0.55);

  const lfo     = audioCtx.createOscillator();
  const lfoGain = audioCtx.createGain();
  lfo.type = 'sine';
  lfo.frequency.setValueAtTime(55, now);
  lfo.frequency.exponentialRampToValueAtTime(10, now + dur * 0.6);
  lfoGain.gain.value = 0.010;
  lfo.connect(lfoGain);

  const noiseGain = audioCtx.createGain();
  lfoGain.connect(noiseGain.gain);
  noiseGain.gain.setValueAtTime(0, now);
  noiseGain.gain.linearRampToValueAtTime(0.022, now + 0.02);
  noiseGain.gain.setValueAtTime(0.022, now + dur * 0.4);
  noiseGain.gain.exponentialRampToValueAtTime(0.001, now + dur);

  noiseSrc.connect(bp);
  bp.connect(noiseGain);
  noiseGain.connect(dest());

  noiseSrc.start(now); noiseSrc.stop(now + dur);
  lfo.start(now);      lfo.stop(now + dur);
}}

function triggerPubGlitch(accentColor, callback) {{
  playPubSwitch();
  const mc   = document.getElementById('map-container');
  const wrap = document.createElement('div');
  wrap.style.cssText = 'position:absolute;inset:0;z-index:10000;pointer-events:none;overflow:hidden';

  const N = 10;
  for (let i = 0; i < N; i++) {{
    const y1    = (Math.random() * 88).toFixed(1);
    const h     = (2 + Math.random() * 10).toFixed(1);
    const dx    = ((Math.random() - 0.5) * 48).toFixed(1);
    const delay = Math.floor(Math.random() * 140);
    const dur   = 100 + Math.floor(Math.random() * 90);
    const s = document.createElement('div');
    s.style.cssText = 'position:absolute;left:-10%;right:-10%;top:' + y1 + '%;height:' + h + '%;background:' + accentColor + ';mix-blend-mode:screen;opacity:0';
    s.animate(
      [
        {{ opacity: 0,    transform: 'translateX(' + dx + 'px)' }},
        {{ opacity: 0.65, transform: 'translateX(' + dx + 'px)' }},
        {{ opacity: 0.4,  transform: 'translateX(' + (dx * 0.35).toFixed(1) + 'px)' }},
        {{ opacity: 0,    transform: 'translateX(0)' }}
      ],
      {{ duration: dur, delay: delay, easing: 'steps(3)', fill: 'forwards' }}
    );
    wrap.appendChild(s);
  }}

  const redDiv  = document.createElement('div');
  const cyanDiv = document.createElement('div');
  redDiv.style.cssText  = 'position:absolute;inset:0;background:rgba(255,20,20,0.16);transform:translateX(7px);mix-blend-mode:screen;opacity:0';
  cyanDiv.style.cssText = 'position:absolute;inset:0;background:rgba(0,240,255,0.12);transform:translateX(-7px);mix-blend-mode:screen;opacity:0';
  [redDiv, cyanDiv].forEach(el => {{
    el.animate(
      [{{ opacity: 1 }}, {{ opacity: 0.5 }}, {{ opacity: 0 }}],
      {{ duration: 220, easing: 'steps(4)', fill: 'forwards' }}
    );
    wrap.appendChild(el);
  }});

  const flash = document.createElement('div');
  flash.style.cssText = 'position:absolute;inset:0;background:rgba(255,255,255,0.06);opacity:0';
  flash.animate(
    [{{ opacity: 0 }}, {{ opacity: 1 }}, {{ opacity: 0 }}],
    {{ duration: 70, easing: 'steps(2)', fill: 'forwards' }}
  );
  wrap.appendChild(flash);

  mc.appendChild(wrap);

  setTimeout(callback, 110);
  setTimeout(() => wrap.remove(), 360);
}}

function triggerClickRipple(clientX, clientY) {{
  const accent =
    getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#2563eb';
  const container = document.getElementById('map-container');
  if (!container) return;
  const rect = container.getBoundingClientRect();
  const x = clientX - rect.left;
  const y = clientY - rect.top;

  const base = {{
    position: 'absolute',
    pointerEvents: 'none',
    zIndex: '10003',
    boxSizing: 'border-box',
    transformOrigin: 'center',
  }};

  const dot = document.createElement('div');
  Object.assign(dot.style, base, {{
    width: '8px',
    height: '8px',
    borderRadius: '50%',
    background: accent,
    left: x - 4 + 'px',
    top: y - 4 + 'px',
  }});
  container.appendChild(dot);
  dot.animate(
    [
      {{ transform: 'scale(1)', opacity: 1, offset: 0 }},
      {{ transform: 'scale(1)', opacity: 1, offset: 80 / 280 }},
      {{ transform: 'scale(0)', opacity: 0, offset: 1 }},
    ],
    {{ duration: 280, easing: 'ease-in', fill: 'forwards' }}
  );
  setTimeout(() => dot.remove(), 300);

  const ring1 = document.createElement('div');
  Object.assign(ring1.style, base, {{
    width: '40px',
    height: '40px',
    borderRadius: '50%',
    border: '1.5px solid ' + accent,
    background: 'transparent',
    left: x - 20 + 'px',
    top: y - 20 + 'px',
  }});
  container.appendChild(ring1);
  ring1.animate(
    [
      {{ transform: 'scale(0.2)', opacity: 0.9, offset: 0 }},
      {{ transform: 'scale(1)', opacity: 0.7, offset: 0.3 }},
      {{ transform: 'scale(2.6)', opacity: 0, offset: 1 }},
    ],
    {{ duration: 520, easing: 'cubic-bezier(0.2, 0.8, 0.4, 1)', fill: 'forwards' }}
  );
  setTimeout(() => ring1.remove(), 540);

  const ring2 = document.createElement('div');
  Object.assign(ring2.style, base, {{
    width: '40px',
    height: '40px',
    borderRadius: '50%',
    border: '1px solid ' + accent,
    opacity: '0.35',
    background: 'transparent',
    left: x - 20 + 'px',
    top: y - 20 + 'px',
  }});
  container.appendChild(ring2);
  ring2.animate(
    [
      {{ transform: 'scale(0.3)', opacity: 0.35, offset: 0 }},
      {{ transform: 'scale(3.4)', opacity: 0, offset: 1 }},
    ],
    {{ duration: 680, delay: 60, easing: 'ease-out', fill: 'forwards' }}
  );
  setTimeout(() => ring2.remove(), 760);
}}

""" + globe_d3_js + f"""

function switchPub(pubId) {{
  if (pubId === currentPub) return;
  currentPub = pubId;

  const meta    = PUB_META[pubId];
  const dataset = DATASETS[pubId];
  const chart   = dataset.chart;

  // Update CSS accent variable
  document.documentElement.style.setProperty('--accent', meta.accent);

  // Update tab active state
  document.querySelectorAll('.pub-tab').forEach(t => {{
    t.classList.toggle('active', t.dataset.pub === pubId);
    t.style.setProperty('--accent', PUB_META[t.dataset.pub].accent);
  }});

  // Update stats bar
  document.getElementById('stat-pub').textContent  = meta.label;
  document.getElementById('stat-arts').textContent = dataset.total;
  document.getElementById('stat-ctry').textContent = dataset.countries;
  document.getElementById('date-range').textContent = dataset.range;
  const lnk = document.getElementById('stat-link');
  lnk.href = meta.url;
  lnk.textContent = meta.label + ' ↗';

  // Populate recent stories panel
  populateRecentPanel(pubId);

  // Build trace
  const maxCount = Math.max(...chart.count, 1);
  const trace = {{
    type: 'choropleth',
    locationmode: 'ISO-3',
    locations: chart.iso3,
    z:         chart.count,
    text:      chart.hover,
    hovertemplate: '%{{text}}<extra></extra>',
    colorscale: meta.colorscale,
    zmin: 1,
    zmax: maxCount,
    showscale: false,
    marker: {{ line: {{ color: '#30363d', width: 0.5 }} }},
    hoverlabel: {{
      font: {{ family: "'Literata', Georgia, serif", size: 13, color: '#f0f6fc' }},
      bgcolor:     '#161b22',
      bordercolor: '#30363d',
    }},
  }};

  const mapDiv = document.getElementById('plotly-map');
  if (!plotlyInited && !plotlyInitializing) {{
    plotlyInitializing = true;
    const plotInitializedFor = pubId;
    Plotly.newPlot(mapDiv, [trace], BASE_LAYOUT, PLOTLY_CONFIG).then(() => {{
      // Trace 1: persistent empty highlight overlay (restyle on article hover)
      Plotly.addTraces(mapDiv, {{
        type:         'choropleth',
        locationmode: 'ISO-3',
        locations:    [],
        z:            [],
        zmin:         0,
        zmax:         1,
        colorscale:   [[0, 'rgba(255,230,0,0)'], [0.25, 'rgba(255,230,0,0.15)'],
                       [1, 'rgba(255,230,0,0.55)']],
        showscale:    false,
        hoverinfo:    'skip',
        marker:       {{ line: {{ color: '#ffe033', width: 2.2 }} }},
      }});

      plotlyInited = true;
      plotlyInitializing = false;

      // User may have switched publication before the first plot finished — sync trace 0.
      if (currentPub !== plotInitializedFor) {{
        const live    = DATASETS[currentPub];
        const liveM   = PUB_META[currentPub];
        const liveMax = Math.max(...live.chart.count, 1);
        Plotly.restyle(mapDiv, {{
          colorscale: [liveM.colorscale],
          zmax:       [liveMax],
          locations:  [live.chart.iso3],
          text:       [live.chart.hover],
        }}, [0])
          .then(() => Plotly.restyle(mapDiv, {{ locations: [[]], z: [[]] }}, [1]))
          .then(() =>
            Plotly.animate(mapDiv, {{
              data:   [{{ z: live.chart.count }}],
              traces: [0],
            }}, {{
              transition: {{ duration: 0 }},
              frame:      {{ duration: 0 }},
            }})
          );
      }}

      mapDiv.on('plotly_hover', data => {{
        const ev = data.event;
        const pt = data.points && data.points[0];
        if (!ev || !pt) return;
        const tt = document.getElementById('map-tooltip');
        if (pt.curveNumber === 0 && pt.location === 'RUS') {{
          const ll = flatMapPointerToLonLat(mapDiv, ev);
          if (isLonLatInCrimeaBox(ll)) {{
            tt.classList.add('crimea-override');
            showTooltip(ev, '<b>Crimea</b><br>Ukrainian territory seized by Russia');
            return;
          }}
        }}
        tt.classList.remove('crimea-override');
        if (pt.text) showTooltip(ev, pt.text);
      }});
      mapDiv.on('plotly_unhover', () => hideTooltip());
      mapDiv.on('plotly_click', data => {{
        if (data.event) triggerClickRipple(data.event.clientX, data.event.clientY);
        const iso3 = data.points[0].location;
        requestAnimationFrame(() => {{
          populateSidebar(iso3);
          playCRTClick();
        }});
      }});
      mapDiv.on('plotly_doubleclick', () => {{
        playCRTClick();
      }});

      Plotly.Plots.resize(mapDiv);
      setTimeout(() => Plotly.Plots.resize(mapDiv), 100);
      let _resizeRaf = null;
      window.addEventListener('resize', () => {{
        if (_resizeRaf) return;
        _resizeRaf = requestAnimationFrame(() => {{
          _resizeRaf = null;
          if (currentProjection === 'globe') resizeGlobe();
          else Plotly.Plots.resize(mapDiv);
        }});
      }});
    }}).catch((err) => {{
      console.error('[plotly]', err);
      plotlyInitializing = false;
    }});
  }} else if (plotlyInited) {{
    triggerPubGlitch(meta.accent, function() {{
      // Always keep Plotly trace 0 in sync regardless of projection
      // so switching back to flat map shows the correct publication
      if (plotlyInited) {{
        Plotly.restyle(mapDiv, {{
          colorscale: [meta.colorscale],
          zmax:       [maxCount],
          locations:  [chart.iso3],
          text:       [chart.hover],
        }}, [0])
          .then(() =>
            Plotly.animate(mapDiv, {{
              data:   [{{ z: chart.count }}],
              traces: [0],
            }}, {{
              transition: {{ duration: currentProjection === 'globe' ? 0 : 380,
                            easing: 'cubic-in-out' }},
              frame:      {{ duration: currentProjection === 'globe' ? 0 : 380 }},
            }})
          );
      }}
      // Also update globe if currently visible
      if (currentProjection === 'globe') {{
        redrawGlobeChoropleth();
      }}
    }});
  }}

  // Update floating overlay and reset sidebar
  updateOverlay(pubId);
  resetSidebar();
  updateGlobeLegendSize();
}}

// ── Sidebar ───────────────────────────────────────────────────────────────
function populateRecentPanel(pubId) {{
  const panel   = document.getElementById('recent-panel');
  const dot     = document.getElementById('recent-pub-dot');
  const label   = document.getElementById('recent-panel-label');
  const dataset = DATASETS[pubId];
  const meta    = PUB_META[pubId];
  const recent  = dataset.recent || [];

  // Accent dot colour = publication accent
  dot.style.background = meta.accent;
  label.textContent    = 'Most Recent — ' + meta.label;

  // Remove old cards (keep the sticky header)
  const hdr = document.getElementById('recent-panel-hdr');
  while (panel.children.length > 1) panel.removeChild(panel.lastChild);

  recent.forEach(art => {{
    const a = document.createElement('a');
    a.className   = 'recent-card';
    a.href        = art.url;
    a.target      = '_blank';
    a.rel         = 'noopener';
    a.innerHTML   = `<div class="recent-card-date">${{art.date}}</div>
                     <div class="recent-card-title">${{art.title}}</div>`;

    a.addEventListener('mouseenter', () => {{
      playArticleHover();
      const cw = getCountryWeightsForArticle(art);
      if (Object.keys(cw).length > 0) highlightCountries(cw);
    }});
    a.addEventListener('mouseleave', () => clearHighlight());

    panel.appendChild(a);
  }});

  if (recent.length === 0) {{
    const empty = document.createElement('div');
    empty.style.cssText = "padding:16px;font-size:0.72rem;color:#6e7681;font-family:'Literata',Georgia,serif";
    empty.textContent   = 'No articles loaded.';
    panel.appendChild(empty);
  }}
}}

function resetSidebar() {{
  document.getElementById('sidebar-country').textContent = 'Select a country';
  document.getElementById('sidebar-count').textContent   = '';
  document.getElementById('sidebar-articles').innerHTML  =
    `<div id="sidebar-empty" style="font-family:'Literata',Georgia,serif">
      <svg width="32" height="32" fill="none" viewBox="0 0 24 24">
        <path stroke="currentColor" stroke-width="1.5" stroke-linecap="round"
              d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 8v4M12 16h.01"/>
      </svg>
      <div>Click a highlighted country<br>on the map to see articles</div>
    </div>`;
  clearHighlight();
}}

// ── Country highlight helpers (driven by article hover) ───────────────────
/** Weights from payload, or derived from countries[] + chart counts (older HTML). */
function getCountryWeightsForArticle(art) {{
  if (art.country_weights && Object.keys(art.country_weights).length > 0)
    return art.country_weights;
  if (!art.countries || !art.countries.length || !currentPub) return {{}};
  const chart = DATASETS[currentPub].chart;
  const out = {{}};
  for (let i = 0; i < art.countries.length; i++) {{
    const iso = art.countries[i];
    const ix  = chart.iso3.indexOf(iso);
    out[iso]  = ix >= 0 ? chart.count[ix] : 1;
  }}
  return out;
}}

function highlightCountries(weights, _attempt) {{
  const w0 = weights && typeof weights === 'object' && !Array.isArray(weights) ? weights : {{}};
  const iso3List = Object.keys(w0);
  if (iso3List.length === 0) {{
    clearHighlight();
    return;
  }}
  const w = {{}};
  for (let i = 0; i < iso3List.length; i++) {{
    const k = iso3List[i];
    w[k] = Number(w0[k]);
  }}
  const maxW = Math.max(...Object.values(w), 1);
  const zVals = iso3List.map((c) => Math.min(1, 0.25 + 0.75 * (w[c] / maxW)));
  if (currentProjection === 'globe') {{
    globeHighlightMap.clear();
    iso3List.forEach((c) => {{
      globeHighlightMap.set(c, w[c] / maxW);
    }});
    if (typeof redrawGlobe === 'function') redrawGlobe();
    return;
  }}
  const mapDiv = document.getElementById('plotly-map');
  const attempt = _attempt || 0;
  if (!mapDiv || !mapDiv.data) return;
  if (mapDiv.data.length < 2) {{
    if (attempt < 90) requestAnimationFrame(() => highlightCountries(w0, attempt + 1));
    return;
  }}
  Plotly.restyle(mapDiv, {{
    locations: [iso3List],
    z:         [zVals],
  }}, [1]);
}}

function clearHighlight() {{
  if (currentProjection === 'globe') {{
    globeHighlightMap.clear();
    if (typeof redrawGlobe === 'function') redrawGlobe();
    return;
  }}
  const mapDiv = document.getElementById('plotly-map');
  if (!mapDiv || !mapDiv.data || mapDiv.data.length < 2) return;
  Plotly.restyle(mapDiv, {{ locations: [[]], z: [[]] }}, [1]);
}}

function populateSidebar(iso3) {{
  if (!currentPub) return;
  const sidebar = DATASETS[currentPub].sidebar;
  const info    = sidebar[iso3];
  if (!info) return;

  clearHighlight();

  const accent = PUB_META[currentPub].accent;
  document.getElementById('sidebar-country').textContent = info.name;
  document.getElementById('sidebar-count').textContent   =
    info.count + ' article' + (info.count !== 1 ? 's' : '') +
    ' in the last {days} days';

  const container = document.getElementById('sidebar-articles');
  container.innerHTML = '';

  info.articles.forEach(art => {{
    const card = document.createElement('a');
    card.className = 'article-card';
    card.href   = art.url;
    card.target = '_blank';
    card.rel    = 'noopener';
    card.innerHTML = `
      <div class="article-date"
           style="font-family:'Literata',Georgia,serif">${{art.date}}</div>
      <span class="article-title"
            style="color:${{accent}};font-family:'Mazius Display','Palatino Linotype',Palatino,serif">${{art.title}}</span>
      <div class="article-summary"
           style="font-family:'Literata',Georgia,serif">${{art.summary}}</div>
    `;

    // Hover → sound + highlight every country mentioned in this article
    card.addEventListener('mouseenter', () => {{
      playArticleHover();
      const cw = getCountryWeightsForArticle(art);
      if (Object.keys(cw).length > 0) highlightCountries(cw);
    }});
    card.addEventListener('mouseleave', () => clearHighlight());

    container.appendChild(card);
  }});
}}

// ── Floating map overlay (created once, updated on every switch) ─────────
const overlay = document.createElement('div');
overlay.id = 'map-overlay';
overlay.style.cssText = `
  position: absolute; top: 16px; left: 16px;
  background: rgba(22,27,34,0.85); backdrop-filter: blur(8px);
  border: 1px solid #30363d; border-radius: 4px;
  padding: 10px 14px; font-size: 0.72rem; color: #8b949e;
  font-family: 'Literata', Georgia, serif;
  pointer-events: none; z-index: 10000;
  transition: border-color 0.3s;
`;

// ── Sound controls bar (mute toggle + volume slider) ─────────────────────
const soundBar = document.createElement('div');
soundBar.style.cssText = `
  position: absolute; bottom: 16px; left: 16px;
  display: flex; align-items: center; gap: 8px;
  background: rgba(22,27,34,0.85); backdrop-filter: blur(8px);
  border: 1px solid #30363d; border-radius: 4px;
  padding: 5px 10px;
  pointer-events: auto; z-index: 10000;
  transition: border-color 0.2s;
`;
soundBar.onmouseenter = () => soundBar.style.borderColor = '#8b949e';
soundBar.onmouseleave = () => soundBar.style.borderColor = '#30363d';

const soundBtn = document.createElement('button');
soundBtn.id          = 'sound-toggle';
soundBtn.textContent = '🔊';
soundBtn.title       = 'Mute sound';
soundBtn.onclick     = toggleSound;
soundBtn.style.cssText = `
  background: none; border: none; padding: 0;
  color: #f0f6fc; font-size: 0.85rem; cursor: pointer;
  transition: opacity 0.2s; line-height: 1;
`;

const volSlider = document.createElement('input');
volSlider.type  = 'range';
volSlider.id    = 'vol-slider';
volSlider.min   = '0';
volSlider.max   = '1';
volSlider.step  = '0.01';
volSlider.value = '0.8';
volSlider.title = 'Volume';
volSlider.style.cssText = `
  -webkit-appearance: none; appearance: none;
  width: 72px; height: 3px;
  background: #30363d; border-radius: 2px; outline: none;
  cursor: pointer; accent-color: #8b949e;
`;
volSlider.oninput = () => {{
  if (_masterGain) _masterGain.gain.setTargetAtTime(
    parseFloat(volSlider.value), audioCtx.currentTime, 0.02
  );
}};

soundBar.appendChild(soundBtn);
soundBar.appendChild(volSlider);
document.getElementById('map-container').appendChild(soundBar);
document.getElementById('map-container').appendChild(overlay);

function updateOverlay(pubId) {{
  const meta    = PUB_META[pubId];
  const dataset = DATASETS[pubId];
  overlay.style.borderColor = meta.accent;
  overlay.innerHTML = `
    <div style="font-family:'Mazius Display','Palatino Linotype',serif;
                font-size:0.85rem; color:#f0f6fc; letter-spacing:0.02em;
                margin-bottom:6px;">${{meta.short}}</div>
    <div style="margin-bottom:2px;">
      <span style="color:#f0f6fc; font-weight:600;">${{dataset.total}}</span>
      &nbsp;articles &nbsp;·&nbsp;
      <span style="color:#f0f6fc; font-weight:600;">${{dataset.countries}}</span>
      &nbsp;countries
    </div>
    <div style="color:#484f58; font-size:0.67rem;">${{dataset.range}}</div>
  `;
}}

// ── Audio engine ──────────────────────────────────────────────────────────
const AudioCtx = window.AudioContext || window.webkitAudioContext;
let audioCtx;
let _masterGain;
let soundEnabled = true;

function ensureCtx() {{
  if (!audioCtx) {{
    audioCtx   = new AudioCtx();
    _masterGain = audioCtx.createGain();
    _masterGain.gain.value = 0.8;
    _masterGain.connect(audioCtx.destination);
  }}
  if (audioCtx.state === 'suspended') audioCtx.resume();
}}

function dest() {{ return _masterGain || audioCtx.destination; }}

// ── Custom tooltip with glitch effect (Plotly hover SVG hidden) ──────────
function showTooltip(ev, html) {{
  const tt        = document.getElementById('map-tooltip');
  const container = document.getElementById('map-container');
  const cRect     = container.getBoundingClientRect();

  tt.innerHTML = html;
  tt.classList.remove('glitching');
  tt.classList.add('visible');
  playHoverStatic();

  requestAnimationFrame(() => {{
    const tw  = tt.offsetWidth;
    const th  = tt.offsetHeight;
    const cw  = container.offsetWidth;
    const ch  = container.offsetHeight;
    const mx  = ev.clientX - cRect.left;
    const my  = ev.clientY - cRect.top;
    const PAD = 10;

    let x = mx + 16;
    let y = my + 16;

    if (x + tw > cw - PAD) x = mx - tw - 16;
    if (y + th > ch - PAD) y = my - th - 16;

    x = Math.max(PAD, Math.min(x, cw - tw - PAD));
    y = Math.max(PAD, Math.min(y, ch - th - PAD));

    tt.style.left = x + 'px';
    tt.style.top  = y + 'px';

    tt.classList.remove('glitching');
    void tt.offsetWidth;
    tt.classList.add('glitching');
    setTimeout(() => tt.classList.remove('glitching'), 380);
  }});
}}

/** Reposition only — same geometry as showTooltip rAF; no sound (globe mousemove within same country). */
function positionTooltip(ev) {{
  const tt        = document.getElementById('map-tooltip');
  if (!tt.classList.contains('visible')) return;
  const container = document.getElementById('map-container');
  const cRect     = container.getBoundingClientRect();
  const tw  = tt.offsetWidth;
  const th  = tt.offsetHeight;
  const cw  = container.offsetWidth;
  const ch  = container.offsetHeight;
  const mx  = ev.clientX - cRect.left;
  const my  = ev.clientY - cRect.top;
  const PAD = 10;

  let x = mx + 16;
  let y = my + 16;
  if (x + tw > cw - PAD) x = mx - tw - 16;
  if (y + th > ch - PAD) y = my - th - 16;
  x = Math.max(PAD, Math.min(x, cw - tw - PAD));
  y = Math.max(PAD, Math.min(y, ch - th - PAD));
  tt.style.left = x + 'px';
  tt.style.top  = y + 'px';
}}

function hideTooltip() {{
  const tt = document.getElementById('map-tooltip');
  tt.classList.remove('visible', 'glitching', 'crimea-override');
}}

// ── Hover: channel tune-in ────────────────────────────────────────────────
function playHoverStatic() {{
  if (!soundEnabled) return;
  ensureCtx();

  const now   = audioCtx.currentTime;
  const dur   = 0.52;
  const sRate = audioCtx.sampleRate;

  // ── Layer 1: white noise swept through a narrowing bandpass ──────────
  const len  = Math.floor(sRate * dur);
  const buf  = audioCtx.createBuffer(1, len, sRate);
  const d    = buf.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;

  const noiseSrc = audioCtx.createBufferSource();
  noiseSrc.buffer = buf;

  // Bandpass sweeps from a hissy mid-band down into a focused low-mid
  // — like the dial slowing as it finds a station
  const bp = audioCtx.createBiquadFilter();
  bp.type = 'bandpass';
  bp.Q.value = 1.1;
  bp.frequency.setValueAtTime(620, now);
  bp.frequency.exponentialRampToValueAtTime(90, now + dur * 0.65);
  bp.frequency.setValueAtTime(90, now + dur * 0.65);

  // AM flutter LFO — fast at first, slows as it settles
  const lfo     = audioCtx.createOscillator();
  const lfoGain = audioCtx.createGain();
  lfo.type = 'sine';
  lfo.frequency.setValueAtTime(38, now);
  lfo.frequency.exponentialRampToValueAtTime(7, now + dur * 0.7);
  lfoGain.gain.value = 0.008;
  lfo.connect(lfoGain);

  const noiseGain = audioCtx.createGain();
  lfoGain.connect(noiseGain.gain);
  noiseGain.gain.setValueAtTime(0, now);
  noiseGain.gain.linearRampToValueAtTime(0.014, now + 0.05);
  noiseGain.gain.setValueAtTime(0.014, now + dur * 0.6);
  noiseGain.gain.exponentialRampToValueAtTime(0.001, now + dur);

  noiseSrc.connect(bp);
  bp.connect(noiseGain);
  noiseGain.connect(dest());

  // ── Layer 2: faint carrier sine that briefly "tunes in" ──────────────
  // Rises near the end like a station signal being found, then fades
  const carrier     = audioCtx.createOscillator();
  const carrierGain = audioCtx.createGain();
  carrier.type = 'sine';
  carrier.frequency.setValueAtTime(95, now);
  carrier.frequency.linearRampToValueAtTime(110, now + dur * 0.75);

  carrierGain.gain.setValueAtTime(0, now);
  carrierGain.gain.linearRampToValueAtTime(0, now + dur * 0.45);
  carrierGain.gain.linearRampToValueAtTime(0.004, now + dur * 0.72);
  carrierGain.gain.exponentialRampToValueAtTime(0.001, now + dur);

  carrier.connect(carrierGain);
  carrierGain.connect(dest());

  noiseSrc.start(now); noiseSrc.stop(now + dur);
  lfo.start(now);      lfo.stop(now + dur);
  carrier.start(now);  carrier.stop(now + dur);
}}

// ── Article hover — "vfooom" doppler-style downward sweep ────────────────
function playArticleHover() {{
  if (!soundEnabled) return;
  ensureCtx();

  const now = audioCtx.currentTime;
  const dur = 0.28;

  // ── Two detuned sawtooth oscillators sweeping pitch down ─────────────
  // Starting mid-range, dropping an octave+ with an exponential ramp —
  // gives the characteristic "vfooom" Doppler pass effect.
  const osc1 = audioCtx.createOscillator();
  const osc2 = audioCtx.createOscillator();
  osc1.type  = 'sine';
  osc2.type  = 'sine';

  osc1.frequency.setValueAtTime(80,  now);
  osc1.frequency.exponentialRampToValueAtTime(14, now + dur);

  // Slight detune — gives it a subtle beating throb
  osc2.frequency.setValueAtTime(83,  now);
  osc2.frequency.exponentialRampToValueAtTime(15, now + dur);

  // ── Lowpass keeps sub-bass clean ──────────────────────────────────────
  const lp  = audioCtx.createBiquadFilter();
  lp.type   = 'lowpass';
  lp.Q.value = 0.8;
  lp.frequency.setValueAtTime(200, now);
  lp.frequency.exponentialRampToValueAtTime(30, now + dur);

  // ── Gain envelope: fast attack, smooth exponential tail ──────────────
  const gain = audioCtx.createGain();
  gain.gain.setValueAtTime(0,      now);
  gain.gain.linearRampToValueAtTime(0.16, now + 0.022);
  gain.gain.exponentialRampToValueAtTime(0.001, now + dur);

  osc1.connect(lp);
  osc2.connect(lp);
  lp.connect(gain);
  gain.connect(dest());

  osc1.start(now); osc1.stop(now + dur);
  osc2.start(now); osc2.stop(now + dur);
}}

// ── Country click — relay thunk ───────────────────────────────────────────
function playCRTClick() {{
  if (!soundEnabled) return;
  ensureCtx();

  const buf  = audioCtx.createBuffer(1, audioCtx.sampleRate * 0.12, audioCtx.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < data.length; i++)
    data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / data.length, 8);

  const source = audioCtx.createBufferSource();
  source.buffer = buf;

  const filter = audioCtx.createBiquadFilter();
  filter.type = 'bandpass';
  filter.frequency.value = 180;
  filter.Q.value = 0.8;

  const gain = audioCtx.createGain();
  gain.gain.setValueAtTime(0.6, audioCtx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.12);

  source.connect(filter);
  filter.connect(gain);
  gain.connect(dest());
  source.start();
}}

// ── Shared convolution reverb (built once, reused) ────────────────────────
let _reverbNode = null;

function getReverbNode() {{
  if (_reverbNode) return _reverbNode;
  ensureCtx();

  // Synthesise an exponentially-decaying stereo impulse response (~1.8 s)
  const sRate   = audioCtx.sampleRate;
  const length  = Math.floor(sRate * 1.8);
  const ir      = audioCtx.createBuffer(2, length, sRate);

  for (let ch = 0; ch < 2; ch++) {{
    const d = ir.getChannelData(ch);
    for (let i = 0; i < length; i++) {{
      // White noise × exponential decay — classic plate-style tail
      const decay = Math.pow(1 - i / length, 4.5);
      d[i] = (Math.random() * 2 - 1) * decay;
    }}
  }}

  const conv = audioCtx.createConvolver();
  conv.buffer = ir;

  // Wet gain — keep reverb tasteful, not washy
  const wetGain = audioCtx.createGain();
  wetGain.gain.value = 0.04;
  conv.connect(wetGain);
  wetGain.connect(dest());

  _reverbNode = conv;
  return conv;
}}

// ── Camera zoom — continuous motor whirr + reverb tail ───────────────────
let _zoomNodes   = null;
let _zoomStopTmr = null;
let _zoomKillTmr = null;   // tracks the delayed osc.stop() call

function startCameraZoom(direction) {{
  if (!soundEnabled) return;
  ensureCtx();

  // Clear any pending stop / kill timers
  if (_zoomStopTmr) {{ clearTimeout(_zoomStopTmr); _zoomStopTmr = null; }}
  if (_zoomKillTmr) {{ clearTimeout(_zoomKillTmr); _zoomKillTmr = null; }}

  // If nodes already exist (including mid-fade-out) — revive + nudge pitch
  if (_zoomNodes) {{
    const target = direction < 0 ? 110 : 72;
    _zoomNodes.osc1.frequency.linearRampToValueAtTime(target,        audioCtx.currentTime + 0.06);
    _zoomNodes.osc2.frequency.linearRampToValueAtTime(target * 1.04, audioCtx.currentTime + 0.06);
    // Cancel fade-out and snap gain back up immediately
    _zoomNodes.outputGain.gain.cancelScheduledValues(audioCtx.currentTime);
    _zoomNodes.outputGain.gain.setTargetAtTime(1, audioCtx.currentTime, 0.03);
    return;
  }}

  // Two detuned sines — sounds like an AF motor
  const osc1 = audioCtx.createOscillator();
  const osc2 = audioCtx.createOscillator();

  // innerGain: LFO modulates THIS, not the output — keeps fade path clean
  const innerGain = audioCtx.createGain();
  innerGain.gain.value = 0.07;

  const lfo     = audioCtx.createOscillator();
  const lfoGain = audioCtx.createGain();
  lfo.frequency.value = 28;
  lfoGain.gain.value  = 0.025;
  lfo.connect(lfoGain);
  lfoGain.connect(innerGain.gain);   // modulates inner, not output

  // outputGain: LFO-free — the only node we touch during fade-out
  const outputGain = audioCtx.createGain();
  outputGain.gain.setValueAtTime(0, audioCtx.currentTime);
  outputGain.gain.linearRampToValueAtTime(1, audioCtx.currentTime + 0.05);

  const baseFreq = direction < 0 ? 110 : 72;
  osc1.type = 'sine'; osc1.frequency.value = baseFreq;
  osc2.type = 'sine'; osc2.frequency.value = baseFreq * 1.04;

  // Gentle lowpass — kill harsh harmonics before reverb
  const lpf = audioCtx.createBiquadFilter();
  lpf.type = 'lowpass'; lpf.frequency.value = 600;

  // Signal chain: oscs → lpf → innerGain → outputGain → dest / reverb
  osc1.connect(lpf); osc2.connect(lpf);
  lpf.connect(innerGain);
  innerGain.connect(outputGain);
  outputGain.connect(dest());
  outputGain.connect(getReverbNode());

  osc1.start(); osc2.start(); lfo.start();
  _zoomNodes = {{ osc1, osc2, lfo, outputGain }};
}}

function stopCameraZoom() {{
  if (!_zoomNodes) return;
  const {{ osc1, osc2, lfo, outputGain }} = _zoomNodes;
  const now = audioCtx.currentTime;

  // Fade outputGain — no LFO on this node, so the decay is perfectly smooth
  outputGain.gain.cancelScheduledValues(now);
  outputGain.gain.setValueAtTime(outputGain.gain.value, now);
  // time constant 0.5 s → at t=2.5 s signal is e^-5 ≈ 0.007 of start → inaudible
  outputGain.gain.setTargetAtTime(0, now, 0.5);

  _zoomKillTmr = setTimeout(() => {{
    try {{ osc1.stop(); osc2.stop(); lfo.stop(); }} catch(_) {{}}
    _zoomNodes   = null;
    _zoomKillTmr = null;
  }}, 2600);
}}

// Wheel: zoom sound (Plotly handles scroll-zoom on map when scrollZoom is enabled)
document.getElementById('plotly-map').addEventListener('wheel', e => {{
  startCameraZoom(e.deltaY);
  if (_zoomStopTmr) clearTimeout(_zoomStopTmr);
  _zoomStopTmr = setTimeout(stopCameraZoom, 300);
}}, {{ passive: true }});

// Double-click sound + sync globe vars — handled via plotly_doubleclick (needs doubleClick:'reset' in config)

// ── Ambient: static drone ─────────────────────────────────────────────────
let _ambientStarted = false;
let _droneGain      = null;

function buildDrone() {{
  // 4-second looping white-noise buffer, heavily lowpass-filtered
  const sRate = audioCtx.sampleRate;
  const len   = sRate * 4;
  const buf   = audioCtx.createBuffer(2, len, sRate);
  for (let ch = 0; ch < 2; ch++) {{
    const d = buf.getChannelData(ch);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
  }}

  const src = audioCtx.createBufferSource();
  src.buffer = buf;
  src.loop   = true;

  // Multi-stage filter: lowpass → lowpass → gentle high-shelf cut
  // gives a thick, distant hiss rather than full-spectrum noise
  const lp1 = audioCtx.createBiquadFilter();
  lp1.type = 'lowpass'; lp1.frequency.value = 320; lp1.Q.value = 0.5;

  const lp2 = audioCtx.createBiquadFilter();
  lp2.type = 'lowpass'; lp2.frequency.value = 200; lp2.Q.value = 0.3;

  _droneGain = audioCtx.createGain();
  _droneGain.gain.value = 0;   // fade in below

  src.connect(lp1);
  lp1.connect(lp2);
  lp2.connect(_droneGain);
  _droneGain.connect(dest());
  src.start();

  // Gentle fade-in over 3 s
  _droneGain.gain.linearRampToValueAtTime(0.045, audioCtx.currentTime + 3);
}}

function startAmbient() {{
  if (_ambientStarted) return;
  _ambientStarted = true;
  buildDrone();
}}

// ── Sound toggle ──────────────────────────────────────────────────────────
function toggleSound() {{
  soundEnabled = !soundEnabled;
  if (!soundEnabled) {{
    if (_zoomNodes) stopCameraZoom();
    if (_droneGain) {{
      _droneGain.gain.cancelScheduledValues(audioCtx.currentTime);
      _droneGain.gain.setValueAtTime(_droneGain.gain.value, audioCtx.currentTime);
      _droneGain.gain.linearRampToValueAtTime(0, audioCtx.currentTime + 0.3);
    }}
  }} else {{
    if (_droneGain) {{
      _droneGain.gain.linearRampToValueAtTime(0.045, audioCtx.currentTime + 0.5);
    }}
  }}
  const btn    = document.getElementById('sound-toggle');
  const slider = document.getElementById('vol-slider');
  btn.textContent    = soundEnabled ? '🔊' : '🔇';
  btn.title          = soundEnabled ? 'Mute sound' : 'Unmute sound';
  btn.style.opacity  = soundEnabled ? '1' : '0.4';
  slider.style.opacity = soundEnabled ? '1' : '0.3';
}}

// Ambient is started by the loader "click to enter" button (browser autoplay compliance).
// Fallback: also start on first scroll/keydown in case loader was already dismissed.
document.addEventListener('keydown', () => {{ if (typeof startAmbient === 'function') {{ ensureCtx(); startAmbient(); }} }}, {{ once: true }});
document.addEventListener('wheel',   () => {{ if (typeof startAmbient === 'function') {{ ensureCtx(); startAmbient(); }} }}, {{ once: true, passive: true }});

// ── Boot: All Coverage aggregate by default ───────────────────────────────
document.querySelectorAll('.pub-tab').forEach(t => {{
  t.style.setProperty('--accent', PUB_META[t.dataset.pub].accent);
}});
switchPub('all');

// ── Fuzzy grain texture for #fuzzy-overlay ───────────────────────────────
(function() {{
  const el = document.getElementById('fuzzy-overlay');
  if (!el) return;
  const SIZE = 180;
  const c = document.createElement('canvas');
  c.width = c.height = SIZE;
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(SIZE, SIZE);
  for (let i = 0; i < img.data.length; i += 4) {{
    const v = Math.random() * 255 | 0;
    img.data[i] = img.data[i+1] = img.data[i+2] = v;
    img.data[i+3] = 255;
  }}
  ctx.putImageData(img, 0, 0);
  el.style.backgroundImage = 'url(' + c.toDataURL() + ')';
}})();

</script>
<div id="credits-modal" style="display:none;position:fixed;inset:0;
  z-index:99998;background:rgba(13,17,23,0.88);backdrop-filter:blur(8px);
  align-items:center;justify-content:center;">
  <div style="background:#161b22;border:1px solid #30363d;border-radius:6px;
    padding:32px 40px;max-width:400px;width:90%;position:relative;
    font-family:'Literata',Georgia,serif;text-align:center;">
    <button onclick="hideCredits()" style="position:absolute;top:12px;right:16px;
      background:none;border:none;color:#484f58;font-size:1.1rem;cursor:pointer;
      font-family:'Literata',Georgia,serif;line-height:1;">✕</button>
    <div style="font-family:'Mazius Display','Palatino Linotype',Palatino,serif;
      font-size:1.4rem;color:#f0f6fc;letter-spacing:0.03em;margin-bottom:6px;">
      Jamestown Foundation
    </div>
    <div style="font-size:0.72rem;color:#8b949e;letter-spacing:0.18em;
      text-transform:uppercase;margin-bottom:24px;">
      Geopolitical Heatmap
    </div>
    <div style="font-size:0.78rem;color:#8b949e;margin-bottom:6px;">
      Created by
    </div>
    <div style="font-family:'Mazius Display','Palatino Linotype',Palatino,serif;
      font-size:1.1rem;color:#f0f6fc;letter-spacing:0.02em;margin-bottom:24px;">
      Schuyler Van Tassel
    </div>
    <div style="font-size:0.68rem;color:#484f58;line-height:1.6;">
      Data sourced from Jamestown Foundation RSS feeds.<br>
      Coverage window: last 90 days.
    </div>
  </div>
</div>
<div id="help-modal" style="display:none;position:fixed;inset:0;
  z-index:99998;background:rgba(13,17,23,0.88);backdrop-filter:blur(8px);
  align-items:center;justify-content:center;">
  <div style="background:#161b22;border:1px solid #30363d;border-radius:6px;
    padding:32px 40px;max-width:480px;width:90%;position:relative;
    font-family:'Literata',Georgia,serif;">
    <button onclick="hideHelp()" style="position:absolute;top:12px;right:16px;
      background:none;border:none;color:#484f58;font-size:1.1rem;cursor:pointer;
      font-family:'Literata',Georgia,serif;line-height:1;">✕</button>
    <div style="font-family:'Mazius Display','Palatino Linotype',Palatino,serif;
      font-size:1.2rem;color:#f0f6fc;letter-spacing:0.03em;margin-bottom:20px;">
      How to Use
    </div>
    <div style="display:flex;flex-direction:column;gap:14px;font-size:0.78rem;
      color:#8b949e;line-height:1.6;">
      <div><span style="color:#f0f6fc;">🗺 Navigate the map</span><br>
        Click and drag to pan. Use the scroll wheel to zoom in and out.</div>
      <div><span style="color:#f0f6fc;">🌍 Switch projections</span><br>
        Toggle between Flat and Globe views using the buttons in the top right.
        On the globe, drag to rotate and scroll to zoom.</div>
      <div><span style="color:#f0f6fc;">🔵 Click a country</span><br>
        Click any shaded country to open a list of articles mentioning it
        in the sidebar. Darker shading means more coverage.</div>
      <div><span style="color:#f0f6fc;">📰 Browse articles</span><br>
        Hover over article cards to highlight all countries mentioned in
        that piece. The highlight intensity reflects how central each
        country is to the publication's overall coverage. Click any card
        to open the full article.</div>
      <div><span style="color:#f0f6fc;">📚 Switch publications</span><br>
        Use the tabs at the top to switch between All Coverage, Eurasia
        Daily Monitor, China Brief, and Terrorism Monitor.</div>
      <div><span style="color:#f0f6fc;">⏸ Globe auto-rotation</span><br>
        The globe rotates automatically. Press Space to pause and resume.
        Drag hard in either direction to flip the rotation direction.</div>
      <div><span style="color:#f0f6fc;">🎧 Sound</span><br>
        Interactive sounds respond to your actions. Use the volume slider
        or mute button in the bottom-left corner to adjust.</div>
    </div>
  </div>
</div>
</body>
</html>"""
    )


# ── Main ──────────────────────────────────────────────────────────────────────


def build_output() -> str:
    all_data = {}
    for pid, pub in PUBLICATIONS.items():
        if pid == "all":
            continue
        print(f"\n  [{pub['short']}] {pub['label']}")
        feed_urls = pub.get("feeds") or [pub["feed"]]
        articles = []
        seen_urls: set[str] = set()
        for feed_url in feed_urls:
            for a in fetch_articles(pid, feed_url, DAYS):
                if a["url"] in seen_urls:
                    continue
                seen_urls.add(a["url"])
                articles.append(a)
        articles.sort(key=lambda x: x["date"], reverse=True)
        country_data = build_country_data(articles)
        all_data[pid] = {"articles": articles, "country_data": country_data}

    # aggregate "all" publication
    merged_seen = set()
    merged_articles = []
    for pid in ["edm", "cb", "tm"]:
        for a in all_data[pid]["articles"]:
            if a["url"] not in merged_seen:
                merged_seen.add(a["url"])
                merged_articles.append(a)
    merged_articles.sort(key=lambda x: x["date"], reverse=True)
    all_data["all"] = {
        "articles": merged_articles,
        "country_data": build_country_data(merged_articles),
    }

    html = build_html(all_data, DAYS)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Saved → {OUTPUT_FILE}")
    return html


def refresh_loop():
    while True:
        time.sleep(REFRESH_HOURS * 3600)
        print(
            f"\n  [auto-refresh] Fetching updated feeds at "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}..."
        )
        try:
            build_output()
            print(f"  [auto-refresh] Complete. Next in {REFRESH_HOURS}h.")
        except Exception as exc:
            print(f"  [auto-refresh] Error: {exc}")


class HeatmapHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/lastmod"):
            try:
                ts = str(int(os.path.getmtime(OUTPUT_FILE)))
            except Exception:
                ts = "0"
            body = ts.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        else:
            # serve the heatmap HTML for any other path
            try:
                with open(OUTPUT_FILE, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self.send_error(404)

    def log_message(self, fmt, *args):
        pass  # suppress request noise


def main():
    print("\n  Jamestown Foundation — Geopolitical Heatmap")
    print("  ═══════════════════════════════════════════════")

    # Initial build
    build_output()

    if not os.environ.get("CI"):
        # Background refresh thread
        rt = threading.Thread(target=refresh_loop, daemon=True)
        rt.start()

        # HTTP server thread
        socketserver.TCPServer.allow_reuse_address = True
        httpd = socketserver.TCPServer(("", PORT), HeatmapHandler)
        ht = threading.Thread(target=httpd.serve_forever, daemon=True)
        ht.start()

        url = f"http://localhost:{PORT}"
        print(f"\n  Serving at {url}")
        print(f"  Auto-refresh every {REFRESH_HOURS}h. Press Ctrl+C to stop.\n")
        webbrowser.open(url)

        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            print("\n  Stopped.")
            httpd.shutdown()


if __name__ == "__main__":
    main()
