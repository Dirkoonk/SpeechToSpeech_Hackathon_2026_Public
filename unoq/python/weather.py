"""Wetter-Cache (Q2.5): Open-Meteo (Nürnberg, 7 Tage, ohne API-Key) alle 30 min
-> data/cache/wetter.json. Bei Fehler bleibt der alte Cache stehen.

Einmalig: python weather.py     Im Programm: weather.start_background()
"""
import datetime
import json
import os
import threading
import urllib.request

from knowledge import DATA

LAT, LON, ORT = 49.45, 11.08, "Nürnberg"
URL = (f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
       "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
       "&timezone=Europe%2FBerlin&forecast_days=7")
CACHE = os.path.join(DATA, "cache", "wetter.json")
INTERVAL_S = 30 * 60

# WMO-Wettercodes, grob zusammengefasst
CODES = [(0, "sonnig"), (2, "leicht bewölkt"), (3, "bewölkt"), (48, "neblig"), (57, "Nieselregen"),
         (67, "Regen"), (77, "Schnee"), (82, "Regenschauer"), (86, "Schneeschauer"), (99, "Gewitter")]


def describe(code):
    for limit, text in CODES:
        if code <= limit:
            return text
    return "wechselhaft"


def fetch():
    with urllib.request.urlopen(URL, timeout=10) as r:
        d = json.load(r)["daily"]
    days = []
    for i, day in enumerate(d["time"]):
        days.append({
            "datum": day,
            "beschreibung": describe(d["weather_code"][i] or 0),
            "min": round(d["temperature_2m_min"][i]),
            "max": round(d["temperature_2m_max"][i]),
            "regen_prozent": d["precipitation_probability_max"][i] or 0,
        })
    data = {"ort": ORT, "stand": datetime.datetime.now().strftime("%d.%m. %H:%M"), "tage": days}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CACHE)  # atomar: Leser sehen nie eine halbe Datei
    return data


def update():
    try:
        fetch()
        return True
    except (OSError, ValueError, KeyError):
        return False  # alten Cache behalten


def start_background(stop_event=None):
    stop_event = stop_event or threading.Event()

    def loop():
        while True:
            update()
            if stop_event.wait(INTERVAL_S):
                return

    threading.Thread(target=loop, name="wetter", daemon=True).start()
    return stop_event


if __name__ == "__main__":
    print("OK" if update() else "Fehler, alter Cache bleibt")
    print(open(CACHE, encoding="utf-8").read()[:600])
