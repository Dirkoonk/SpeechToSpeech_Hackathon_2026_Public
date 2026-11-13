"""Wissensspeicher (Q2.4): Intent per Schlüsselwort -> passende Daten für den LLM-Prompt.

Quellen: data/station.json, data/witze.json, data/cache/wetter.json (siehe weather.py).
Kein Treffer -> "" (die Pipeline antwortet dann fest mit NO_INFO, ohne LLM).
"""
import datetime
import json
import os
import random
import re

DATA = os.environ.get("LINGO_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data"))

_cache = {}


def _load(name):
    path = os.path.join(DATA, name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _cache.get(name)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _cache[name] = (mtime, data)
    return data


def normalize(text):
    t = text.lower()
    t = t.replace("ß", "ss")
    t = re.sub(r"[^\wäöü ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Schlüsselwort-Stämme je Intent (Vosk liefert klein geschrieben, ohne Satzzeichen)
INTENTS = [
    ("besuch", ["besuch"]),
    ("essen", ["essen", "frühstück", "fruehstueck", "mittag", "abendbrot", "abendessen", "mahlzeit", "hunger"]),
    ("wetter", ["wetter", "regen", "regnet", "sonne", "temperatur", "warm", "kalt", "schnee"]),
    ("witz", ["witz", "lustig", "lachen"]),
    ("wlan", ["wlan", "internet", "wifi", "passwort"]),
    ("ruhe", ["ruhe", "nachtruhe", "mittagsruhe", "schlafen"]),
    ("kontakt", ["pflege", "schwester", "pfleger", "notfall", "klingel", "hilfe", "arzt"]),
    ("weg", ["wo ist", "wo finde", "wie komme", "weg", "cafeteria", "radiologie", "apotheke", "ausgang",
             "kapelle", "röntgen", "kantine"]),
    ("station", ["station", "wo bin"]),
]


def intent(text):
    t = normalize(text)
    for name, keys in INTENTS:
        if any(k in t for k in keys):
            return name
    return None


def _weather_text():
    w = _load(os.path.join("cache", "wetter.json"))
    if not w:
        return ""
    days = w.get("tage", [])
    today = datetime.date.today().isoformat()
    names = {0: "Heute", 1: "Morgen", 2: "Übermorgen"}
    lines = []
    for d in days:
        try:
            delta = (datetime.date.fromisoformat(d["datum"]) - datetime.date.fromisoformat(today)).days
        except (KeyError, ValueError):
            continue
        if 0 <= delta <= 6:
            label = names.get(delta, d["datum"])
            lines.append(f"{label}: {d['beschreibung']}, {d['min']} bis {d['max']} Grad, "
                         f"Regenwahrscheinlichkeit {d['regen_prozent']} Prozent.")
    if lines:
        lines.insert(0, f"Wetter in {w.get('ort', 'Nürnberg')} (Stand {w.get('stand', '?')}):")
    return "\n".join(lines[:5])


def phrase(text, src):
    """Q2.6: Geprüfte Übersetzung aus data/phrasen_de_en.json (normalisierter Vergleich) oder None."""
    dst = "en" if src == "de" else "de"
    n = normalize(text)
    for p in (_load("phrasen_de_en.json") or {}).get("phrasen", []):
        if p.get(src) and normalize(p[src]) == n:
            return p.get(dst)
    return None


def lookup(question):
    """Frage -> Datentext für den Prompt ("" = keine Information)."""
    name = intent(question)
    if name is None:
        return ""
    if name == "witz":
        w = _load("witze.json") or {}
        jokes = w.get("witze", [])
        return f"Erzähle genau diesen Witz: {random.choice(jokes)}" if jokes else ""
    if name == "wetter":
        return _weather_text()
    s = _load("station.json") or {}
    if name == "besuch":
        return f"Besuchszeiten: {s.get('besuchszeiten', '')}"
    if name == "essen":
        e = s.get("essenszeiten", {})
        return (f"Frühstück: {e.get('fruehstueck')}. Mittagessen: {e.get('mittagessen')}. "
                f"Abendessen: {e.get('abendessen')}.")
    if name == "wlan":
        return s.get("wlan_gaeste", "")
    if name == "ruhe":
        return s.get("ruhezeiten", "")
    if name == "kontakt":
        k = s.get("kontakt", {})
        return f"Pflege: {k.get('pflege')} Notfall: {k.get('notfall')}"
    if name == "station":
        return f"Du bist auf der {s.get('station', '')}."
    if name == "weg":
        wege = s.get("wege", {})
        t = normalize(question)
        hits = {k: v for k, v in wege.items() if k in t or (k == "radiologie" and "röntgen" in t)
                or (k == "cafeteria" and "kantine" in t)}
        chosen = hits or wege
        return "Wege: " + " ".join(f"{k.capitalize()}: {v}" for k, v in chosen.items())
    return ""


def _all_ways():
    wege = (_load("station.json") or {}).get("wege", {})
    return "Wege: " + " ".join(f"{k.capitalize()}: {v}" for k, v in wege.items()) if wege else ""


def topic(question):
    """Frage -> (Thema, Datentext). Wie lookup(), aber der Datentext eines Themas ist immer gleich
    (bei Wegen: alle Wege), damit das Sprachmodell ihn vorab lesen und im Speicher behalten kann.
    Thema "witz": Datentext ist der fertige Witz (wird ohne Sprachmodell gesprochen)."""
    name = intent(question)
    if name is None:
        return None, ""
    if name == "witz":
        jokes = (_load("witze.json") or {}).get("witze", [])
        return name, (random.choice(jokes) if jokes else "")
    if name == "weg":
        return name, _all_ways()
    return name, lookup(question)


# Reihenfolge = Reihenfolge beim Vorladen (häufige Fragen zuerst)
PRELOAD = [("besuch", "besuchszeiten"), ("essen", "mittagessen"), ("weg", "wo ist"), ("wetter", "wetter"),
           ("kontakt", "pflege"), ("wlan", "wlan"), ("ruhe", "nachtruhe"), ("station", "station")]


def topics():
    """[(Thema, Datentext)] aller Themen mit festen Daten – zum Vorladen ins Sprachmodell."""
    out = []
    for name, probe in PRELOAD:
        data = _all_ways() if name == "weg" else lookup(probe)
        if data:
            out.append((name, data))
    return out


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Wann sind die Besuchszeiten?"
    print(intent(q), "->", lookup(q))
