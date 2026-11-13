#!/usr/bin/env python3
"""Benchmark-Lauf auf dem UNO Q (Aufgabe B.2): spielt die aufgenommenen Testsätze durch die
echte Pipeline (Vosk -> Phrasenbibliothek/LLM -> Piper) und schreibt benchmarks/results.csv.

Voraussetzungen auf dem UNO Q
    * Repo ist synchronisiert (unoq/python/ liegt neben benchmarks/), Modelle unter /home/arduino/models
    * llama-server läuft auf :8080 (unoq/start_llama.sh)
    * WAVs liegen in benchmarks/audio/T01.wav ... T10.wav (16 kHz mono, siehe record_testsaetze.py)

Ablauf
    1. KALT: erste LLM-Anfrage direkt nach dem Start, ohne Aufwärmen (warm=0). Für einen sauberen
       Kaltwert llama-server vorher neu starten:  --kalt  (nur diese eine Messung, dann Ende)
    2. Aufwärmen (System-Prompts in den KV-Cache, Piper-Stimmen, Vosk-Modelle)
    3. WARM: jeder Testsatz --reps mal (Standard 3), warm=1
    4. Zusätzlich T06/T07 mit erzwungener LLM-Übersetzung (Vergleich Phrasenbibliothek vs. LLM)

Aufruf
    python benchmarks/run_benchmark.py                 # Aufwärmen + 3 Wiederholungen + LLM-Vergleich
    python benchmarks/run_benchmark.py --kalt          # nur Kaltmessung (llama-server vorher neu starten!)
    python benchmarks/run_benchmark.py --only T06 T09 --reps 1
    python benchmarks/run_benchmark.py --listen        # Wiedergabe über den Lautsprecher (Standard: stumm)

Es wird kein Audio gespeichert (nur gelesen). Die Ausgabe ist nur Zeitmessung, keine Patientendaten.
"""
import argparse
import csv
import datetime
import os
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "unoq" / "python"))
sys.path.insert(0, os.environ.get("LINGO_PY", str(ROOT / "unoq" / "python")))

TESTSAETZE = ROOT / "data" / "testsaetze.txt"
AUDIO_DIR = ROOT / "benchmarks" / "audio"
CSV_COLS = ["timestamp", "test_id", "variante", "stt_engine", "uebersetzer", "warm", "wiederholung",
            "stt_wall_s", "stt_cpu_s", "llm_prompt_tokens", "llm_gen_tokens", "llm_wall_s", "llm_cpu_s",
            "tts_wall_s", "tts_cpu_s", "gesamt_wall_s", "energie_wh_modelliert", "energie_wh_gemessen", "notiz"]


def lade_saetze():
    saetze = []
    for zeile in TESTSAETZE.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#"):
            continue
        t = [x.strip() for x in zeile.split("|", 3)]
        if len(t) == 4:
            saetze.append({"id": t[0], "modus": t[1], "sprache": t[2], "text": t[3]})
    return saetze


def lies_wav(pfad):
    with wave.open(str(pfad), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            sys.exit(f"{pfad.name}: erwartet 16 kHz, mono, 16 Bit (gefunden: {w.getframerate()} Hz, "
                     f"{w.getnchannels()} Kanäle, {8 * w.getsampwidth()} Bit)")
        return w.readframes(w.getnframes())


def stt_messen(pl, pcm, lang):
    """Vosk über die ganze Aufnahme. Gibt (Text, Wall-s, CPU-s) zurück."""
    w0, c0 = time.monotonic(), time.process_time()
    text = pl.stt.transcribe(pcm, lang)
    return text, time.monotonic() - w0, time.process_time() - c0


def lauf(pl, s, pcm, rep, warm, llm_erzwingen=False):
    """Ein Messlauf für einen Testsatz. Gibt eine CSV-Zeile (dict) zurück."""
    import knowledge
    import s2s_pipeline as s2s

    text, stt_wall, stt_cpu = stt_messen(pl, pcm, s["sprache"])
    t0 = time.monotonic()
    uebersetzer = "-"
    if s["modus"] == "assistent":
        antwort, st = pl.chat(text, knowledge.lookup(text))
    else:
        src = s["sprache"]
        dst = "en" if src == "de" else "de"
        if llm_erzwingen:
            msgs = [{"role": "system", "content": s2s.SYS_TRANSLATE},
                    {"role": "user", "content": s2s.TRANSLATE_PREFIX[(src, dst)] + text}]
            antwort, st = pl._run_llm(msgs, dst, max_tokens=100)
            st["uebersetzer"] = "llm"
        else:
            antwort, st = pl.translate(text, src, dst)
        uebersetzer = st.pop("uebersetzer", "llm")
    gesamt = time.monotonic() - t0 + stt_wall

    zeile = {k: "" for k in CSV_COLS}
    zeile.update({k: v for k, v in st.items() if k in CSV_COLS and v is not None})
    zeile.update(
        timestamp=datetime.datetime.now().isoformat(timespec="seconds"), test_id=s["id"], variante=s["modus"],
        stt_engine="vosk", uebersetzer=uebersetzer, warm=int(warm), wiederholung=rep,
        stt_wall_s=round(stt_wall, 3), stt_cpu_s=round(stt_cpu, 3), gesamt_wall_s=round(gesamt, 3),
        notiz=(f"erkannt='{text}'" + (" llm_erzwungen" if llm_erzwingen else "")
               + f" erste_audio_s={st.get('first_audio_s')}"),
    )
    return zeile


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=3, help="Wiederholungen pro Testsatz (warm), Standard 3")
    ap.add_argument("--only", nargs="+", metavar="ID", help="nur diese Testsatz-IDs")
    ap.add_argument("--kalt", action="store_true", help="nur die Kaltmessung (erste Anfrage ohne Aufwärmen)")
    ap.add_argument("--keine-llm-variante", action="store_true", help="T06/T07 nicht zusätzlich mit LLM messen")
    ap.add_argument("--listen", action="store_true", help="Antworten über den Lautsprecher ausgeben")
    ap.add_argument("--out", default=str(ROOT / "benchmarks" / "results.csv"))
    a = ap.parse_args()

    try:
        import s2s_pipeline as s2s
    except ImportError as e:
        sys.exit(f"Pipeline nicht importierbar ({e}). Läuft das Skript auf dem UNO Q im Repo?")
    s2s.DEBUG = False

    saetze = lade_saetze()
    if a.only:
        wanted = {x.upper() for x in a.only}
        saetze = [s for s in saetze if s["id"].upper() in wanted]
    fehlt = [s["id"] for s in saetze if not (AUDIO_DIR / f"{s['id']}.wav").exists()]
    if fehlt:
        sys.exit("Aufnahmen fehlen in benchmarks/audio/: " + ", ".join(fehlt))
    audio = {s["id"]: lies_wav(AUDIO_DIR / f"{s['id']}.wav") for s in saetze}

    pl = s2s.Pipeline(audible=a.listen)
    if not pl.llm.health():
        sys.exit("llama-server antwortet nicht auf :8080 (unoq/start_llama.sh starten).")

    neu = not os.path.exists(a.out)
    f = open(a.out, "a", newline="", encoding="utf-8")
    w = csv.DictWriter(f, fieldnames=CSV_COLS)
    if neu:
        w.writeheader()

    def schreibe(z):
        w.writerow(z)
        f.flush()
        print(f"{z['test_id']} {z['variante']:<11} {z['uebersetzer']:<7} warm={z['warm']} rep={z['wiederholung']} "
              f"gesamt={z['gesamt_wall_s']}s  ({z['notiz'][:60]})")

    # 1) Kalt: erste LLM-Anfrage, nichts aufgewärmt (ein LLM-Satz: erster mit Dolmetscher/Assistent-Daten)
    kalt_satz = next((s for s in saetze if s["id"] == "T09"), None) or next(
        (s for s in saetze if s["modus"] == "dolmetscher" and s["id"] not in ("T06", "T07")), saetze[0])
    print("== Kaltmessung ==", kalt_satz["id"])
    schreibe(lauf(pl, kalt_satz, audio[kalt_satz["id"]], 0, warm=False))
    if a.kalt:
        f.close()
        return

    # 2) Aufwärmen
    print("== Aufwärmen ==")
    pl.llm.warmup(s2s.SYS_CHAT)
    pl.llm.warmup(s2s.SYS_TRANSLATE)
    for lang in ("de", "en"):
        pl.tts.warmup(lang)
    for lang in ("de", "en"):
        pl.stt.model(lang)

    # 3) Warm: alle Testsätze, reps Wiederholungen
    print("== Warmmessung ==")
    for rep in range(1, a.reps + 1):
        for s in saetze:
            schreibe(lauf(pl, s, audio[s["id"]], rep, warm=True))

    # 4) Phrasenbibliothek vs. LLM: dieselben Sätze, LLM erzwungen
    if not a.keine_llm_variante:
        print("== LLM-Übersetzung erzwungen (Vergleich Phrasenbibliothek) ==")
        for rep in range(1, a.reps + 1):
            for s in saetze:
                if s["id"] in ("T06", "T07"):
                    schreibe(lauf(pl, s, audio[s["id"]], rep, warm=True, llm_erzwingen=True))
    f.close()
    print("Fertig:", a.out)


if __name__ == "__main__":
    main()
