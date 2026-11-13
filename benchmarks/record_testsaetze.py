#!/usr/bin/env python3
"""Nimmt die 10 Testsätze aus data/testsaetze.txt mit dem Mikrofon auf (Aufgabe B.1).

Ausgabe: benchmarks/audio/T01.wav ... (16 kHz, mono, 16 Bit). Der Ordner steht in
.gitignore: Aufnahmen werden nicht committet (nur für Benchmarks, keine Patientendaten).

Bedienung (pro Satz):
    Enter       Aufnahme starten, danach nochmal Enter zum Stoppen
    w           letzte Aufnahme dieses Satzes abspielen
    r           neu aufnehmen
    s           Satz überspringen
    q           Beenden
Bereits vorhandene Dateien werden übersprungen (mit --again neu aufnehmen).

Benötigt:  pip install sounddevice
Beispiele: python benchmarks/record_testsaetze.py --list
           python benchmarks/record_testsaetze.py --device 3
           python benchmarks/record_testsaetze.py --only T06 T07 --again
"""
import argparse
import queue
import sys
import threading
import wave
from pathlib import Path

RATE = 16000
ROOT = Path(__file__).resolve().parent.parent
TESTSAETZE = ROOT / "data" / "testsaetze.txt"
AUDIO_DIR = ROOT / "benchmarks" / "audio"


def lade_saetze():
    saetze = []
    for zeile in TESTSAETZE.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#"):
            continue
        teile = [t.strip() for t in zeile.split("|", 3)]
        if len(teile) == 4:
            saetze.append({"id": teile[0], "modus": teile[1], "sprache": teile[2], "text": teile[3]})
    return saetze


def schreibe_wav(pfad, daten):
    with wave.open(str(pfad), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(daten)


def nimm_auf(sd, device):
    """Nimmt auf, bis der Nutzer Enter drückt. Gibt rohe int16-Bytes zurück."""
    q = queue.Queue()

    def callback(indata, frames, time_info, status):
        q.put(bytes(indata))

    stop = threading.Event()
    threading.Thread(target=lambda: (input(), stop.set()), daemon=True).start()
    teile = []
    with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", device=device, callback=callback):
        while not stop.is_set():
            try:
                teile.append(q.get(timeout=0.1))
            except queue.Empty:
                pass
        while not q.empty():
            teile.append(q.get_nowait())
    return b"".join(teile)


def spiele_ab(sd, pfad):
    with wave.open(str(pfad), "rb") as w:
        daten = w.readframes(w.getnframes())
    with sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16") as out:
        out.write(daten)


def pegel(daten):
    """Spitzenpegel in Prozent von Vollaussteuerung (ohne numpy)."""
    if not daten:
        return 0.0
    import array
    a = array.array("h")
    a.frombytes(daten[: len(daten) // 2 * 2])
    return 100.0 * max(abs(x) for x in a) / 32768.0 if a else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="Audio-Eingabegeräte anzeigen und beenden")
    ap.add_argument("--device", help="Index oder Name des Eingabegeräts (siehe --list)")
    ap.add_argument("--only", nargs="+", metavar="ID", help="nur diese IDs aufnehmen, z. B. T06 T07")
    ap.add_argument("--again", action="store_true", help="vorhandene Aufnahmen neu aufnehmen")
    args = ap.parse_args()

    try:
        import sounddevice as sd
    except ImportError:
        sys.exit("sounddevice fehlt:  pip install sounddevice")

    if args.list:
        print(sd.query_devices())
        return

    device = args.device
    if device is not None and device.isdigit():
        device = int(device)

    saetze = lade_saetze()
    if args.only:
        gewuenscht = {i.upper() for i in args.only}
        saetze = [s for s in saetze if s["id"].upper() in gewuenscht]
    if not saetze:
        sys.exit(f"Keine Testsätze gefunden in {TESTSAETZE}")

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Aufnahmen nach {AUDIO_DIR}  ({RATE} Hz, mono). Ruhiger Raum, ca. 20 cm Abstand zum Mikrofon.")
    print("Enter = starten/stoppen · w = abspielen · r = neu · s = überspringen · q = beenden\n")

    for i, s in enumerate(saetze, 1):
        pfad = AUDIO_DIR / f"{s['id']}.wav"
        if pfad.exists() and not args.again:
            print(f"[{i}/{len(saetze)}] {s['id']} vorhanden, übersprungen")
            continue
        while True:
            print(f"[{i}/{len(saetze)}] {s['id']} ({s['modus']}, {s['sprache']}):")
            print(f"    \"{s['text']}\"")
            eingabe = input("    Enter zum Starten (s = überspringen, q = beenden): ").strip().lower()
            if eingabe == "q":
                return
            if eingabe == "s":
                break
            print("    ● Aufnahme läuft ... sprechen, dann Enter zum Stoppen")
            daten = nimm_auf(sd, device)
            dauer = len(daten) / 2 / RATE
            p = pegel(daten)
            hinweis = ""
            if dauer < 0.5:
                hinweis = "  (sehr kurz!)"
            elif p < 5:
                hinweis = "  (sehr leise: näher ans Mikrofon oder Eingangspegel erhöhen)"
            elif p > 98:
                hinweis = "  (übersteuert: leiser sprechen oder Pegel senken)"
            print(f"    {dauer:.1f} s, Spitzenpegel {p:.0f} %{hinweis}")
            schreibe_wav(pfad, daten)
            while True:
                a = input("    Enter = übernehmen · w = anhören · r = neu aufnehmen: ").strip().lower()
                if a == "w":
                    try:
                        spiele_ab(sd, pfad)
                    except Exception as e:  # noqa: BLE001
                        print("    Abspielen nicht möglich:", e)
                    continue
                break
            if a == "r":
                continue
            print(f"    gespeichert: {pfad.name}\n")
            break

    fehlend = [s["id"] for s in lade_saetze() if not (AUDIO_DIR / f"{s['id']}.wav").exists()]
    print("Fertig." if not fehlend else "Noch ohne Aufnahme: " + ", ".join(fehlend))


if __name__ == "__main__":
    main()
