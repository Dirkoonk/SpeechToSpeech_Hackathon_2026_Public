#!/usr/bin/env python3
"""Wertet benchmarks/results.csv aus (Aufgabe B.3): Mediane, Energie pro Interaktion (modelliert),
SCI pro Interaktion und Faktor gegenüber Dauerlauschen. Nur Standardbibliothek.

Modell (docs/BENCHMARKS_SCI.md), als "modelliert" kennzeichnen:
    P(t) = P_idle + (P_peak - P_idle) * CPU-Auslastung      CPU-Auslastung = cpu_s / (wall_s * Kerne)
    E_Interaktion [Wh] = Summe über Stufen (STT, LLM, TTS) von P * wall_s / 3600
    SCI = (E * I + M) pro R       E in kWh, I in gCO2e/kWh, M anteilig, R = eine Sprachinteraktion

Vergleich Dauerlauschen (Variante A) gegen Wake-Word auf dem Nicla (Variante B), je 24 h:
    A: UNO Q lauscht dauernd mit Vosk:    24 h * P_stt_dauer + Interaktionen * E_Interaktion
    B: UNO Q im Leerlauf + Nicla lauscht:  24 h * (P_idle + P_nicla) + Interaktionen * E_Interaktion

Aufruf (Beispiel):
    python benchmarks/auswertung.py --intensitaet 380 --embodied-kg 20 --interaktionen 40
Werte mit "ANNAHME" markieren und in der Präsentation so nennen. I (CO2-Intensität DE) selbst nachschlagen
(UBA oder Electricity Maps) und die Quelle notieren; das Skript erfindet keinen Wert.
"""
import argparse
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def zahl(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def median(werte):
    w = [x for x in werte if x is not None]
    return statistics.median(w) if w else None


def last_leistung(cpu_s, wall_s, p_idle, p_peak, kerne):
    """Leistung in W für eine Stufe. Ohne Messwerte: Leerlauf."""
    if not wall_s or cpu_s is None:
        return p_idle, 0.0
    u = max(0.0, min(1.0, cpu_s / (wall_s * kerne)))
    return p_idle + (p_peak - p_idle) * u, u


def energie_zeile(z, p_idle, p_peak, kerne):
    """Energie [Wh] einer Messzeile (Median-Zeile) und Einzelwerte je Stufe."""
    stufen = {}
    gesamt = 0.0
    for name in ("stt", "llm", "tts"):
        wall = z.get(f"{name}_wall_s") or 0.0
        cpu = z.get(f"{name}_cpu_s")
        p, u = last_leistung(cpu, wall, p_idle, p_peak, kerne)
        e = p * wall / 3600.0
        stufen[name] = {"wall": wall, "cpu": cpu, "util": u, "P": p, "Wh": e}
        gesamt += e
    return gesamt, stufen


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=str(ROOT / "benchmarks" / "results.csv"))
    ap.add_argument("--p-idle", type=float, default=0.5, help="W, Leerlauf UNO Q (Richtwert Rovai 2026)")
    ap.add_argument("--p-peak", type=float, default=3.5, help="W, Volllast UNO Q (Richtwert Rovai 2026)")
    ap.add_argument("--kerne", type=int, default=4, help="CPU-Kerne der MPU")
    ap.add_argument("--p-nicla", type=float, default=0.005,
                    help="W, Nicla beim Wake-Word-Lauschen. ANNAHME 5 mW, Datenblatt/Messung prüfen")
    ap.add_argument("--interaktionen", type=int, default=40, help="ANNAHME: Sprachinteraktionen pro 24 h")
    ap.add_argument("--intensitaet", type=float, help="gCO2e/kWh Strommix Deutschland (selbst nachschlagen!)")
    ap.add_argument("--embodied-kg", type=float, help="kg CO2e Herstellung UNO Q + Nicla (ANNAHME, Quelle nennen)")
    ap.add_argument("--lebensdauer-jahre", type=float, default=3.0, help="ANNAHME Lebensdauer")
    ap.add_argument("--md", help="Ergebnis zusätzlich als Markdown-Datei speichern")
    a = ap.parse_args()

    pfad = Path(a.csv)
    if not pfad.exists():
        sys.exit(f"{pfad} fehlt. Erst benchmarks/run_benchmark.py laufen lassen.")
    zeilen = list(csv.DictReader(open(pfad, encoding="utf-8")))
    if not zeilen:
        sys.exit("results.csv enthält keine Zeilen.")

    num_cols = ["stt_wall_s", "stt_cpu_s", "llm_wall_s", "llm_cpu_s", "tts_wall_s", "tts_cpu_s", "gesamt_wall_s",
                "llm_prompt_tokens", "llm_gen_tokens"]
    # Gruppieren: (Testsatz, Variante, Übersetzer, warm, LLM erzwungen)
    gruppen = defaultdict(list)
    for r in zeilen:
        erzwungen = "llm_erzwungen" in (r.get("notiz") or "")
        gruppen[(r["test_id"], r["variante"], r["uebersetzer"], r["warm"], erzwungen)].append(r)

    med = {}
    for key, rs in gruppen.items():
        med[key] = {c: median([zahl(r.get(c)) for r in rs]) for c in num_cols}
        med[key]["n"] = len(rs)

    ausgabe = []

    def p(*t):
        s = " ".join(str(x) for x in t)
        print(s)
        ausgabe.append(s)

    p("# Auswertung Benchmarks (Mediane, Energie **modelliert**)\n")
    p(f"Quelle: {pfad.name}, {len(zeilen)} Zeilen. Lastmodell: P_idle={a.p_idle} W, P_peak={a.p_peak} W, "
      f"{a.kerne} Kerne.\n")

    # --- Tabelle je Testsatz (warm, Standardweg)
    p("## Warm, Standardweg (Phrasenbibliothek wenn Treffer)\n")
    p("| Test | Variante | Übersetzer | n | STT s | LLM s | TTS s | Gesamt s | Energie mWh |")
    p("|---|---|---|---|---|---|---|---|---|")
    energien_warm = []
    for key in sorted(med):
        tid, var, ue, warm, erz = key
        if warm != "1" or erz:
            continue
        m = med[key]
        e, _ = energie_zeile({c: m[c] for c in num_cols}, a.p_idle, a.p_peak, a.kerne)
        energien_warm.append(e)
        p(f"| {tid} | {var} | {ue} | {m['n']} | {m['stt_wall_s']:.2f} | {(m['llm_wall_s'] or 0):.2f} | "
          f"{(m['tts_wall_s'] or 0):.2f} | {m['gesamt_wall_s']:.2f} | {e * 1000:.3f} |")
    if not energien_warm:
        sys.exit("Keine Warm-Zeilen (warm=1) gefunden.")
    e_int_wh = statistics.median(energien_warm)
    t_int_s = median([med[k]["gesamt_wall_s"] for k in med if k[3] == "1" and not k[4]])

    # --- Kalt vs. warm
    kalt = [k for k in med if k[3] == "0"]
    if kalt:
        p("\n## Kalt vs. aufgewärmt\n")
        p("| Test | Kalt Gesamt s | Warm Gesamt s | Faktor |")
        p("|---|---|---|---|")
        for k in sorted(kalt):
            w = med.get((k[0], k[1], k[2], "1", False))
            if w and w["gesamt_wall_s"]:
                p(f"| {k[0]} | {med[k]['gesamt_wall_s']:.2f} | {w['gesamt_wall_s']:.2f} | "
                  f"{med[k]['gesamt_wall_s'] / w['gesamt_wall_s']:.1f}x |")
            else:
                p(f"| {k[0]} | {med[k]['gesamt_wall_s']:.2f} | (keine Warm-Messung) | |")

    # --- Phrasenbibliothek vs. LLM
    verglichen = [k for k in med if k[4] and k[3] == "1"]
    if verglichen:
        p("\n## Phrasenbibliothek vs. LLM-Übersetzung (warm)\n")
        p("| Test | Phrasen Gesamt s | LLM Gesamt s | Faktor | Phrasen mWh | LLM mWh |")
        p("|---|---|---|---|---|---|")
        for k in sorted(verglichen):
            ph = med.get((k[0], k[1], "phrasen", "1", False))
            if not ph:
                continue
            ml = med[k]
            e_l, _ = energie_zeile({c: ml[c] for c in num_cols}, a.p_idle, a.p_peak, a.kerne)
            e_p, _ = energie_zeile({c: ph[c] for c in num_cols}, a.p_idle, a.p_peak, a.kerne)
            p(f"| {k[0]} | {ph['gesamt_wall_s']:.2f} | {ml['gesamt_wall_s']:.2f} | "
              f"{ml['gesamt_wall_s'] / ph['gesamt_wall_s']:.1f}x | {e_p * 1000:.3f} | {e_l * 1000:.3f} |")

    # --- Dauerlauschen vs. Wake-Word
    stt_util = median([last_leistung(med[k]["stt_cpu_s"], med[k]["stt_wall_s"], a.p_idle, a.p_peak, a.kerne)[1]
                       for k in med if k[3] == "1" and not k[4]])
    p_stt_dauer = a.p_idle + (a.p_peak - a.p_idle) * (stt_util or 0.0)
    e_ints = a.interaktionen * e_int_wh
    e_a = 24 * p_stt_dauer + e_ints                       # Wh pro 24 h
    e_b = 24 * (a.p_idle + a.p_nicla) + e_ints
    p("\n## Dauerlauschen vs. Wake-Word auf dem Nicla (24 h, modelliert)\n")
    p(f"- Energie pro Interaktion (Median, warm): **{e_int_wh * 1000:.2f} mWh** bei {t_int_s:.1f} s "
      f"Gesamtdauer")
    p(f"- STT-CPU-Auslastung (Median): {100 * (stt_util or 0):.0f} %  ->  Dauerlauschen mit Vosk ca. "
      f"{p_stt_dauer:.2f} W")
    p(f"- ANNAHME {a.interaktionen} Interaktionen/Tag; ANNAHME Nicla {a.p_nicla * 1000:.1f} mW")
    p(f"- A  Dauerlauschen auf dem UNO Q: **{e_a:.1f} Wh / 24 h**")
    p(f"- B  Wake-Word auf dem Nicla:    **{e_b:.1f} Wh / 24 h**")
    p(f"- **Faktor A/B: {e_a / e_b:.1f}x weniger Energie** mit Nicla")

    # --- SCI
    p("\n## SCI pro Interaktion (modelliert)\n")
    if a.intensitaet is None:
        p("`--intensitaet` fehlt (gCO2e/kWh, Strommix Deutschland, Quelle UBA oder Electricity Maps). "
          "SCI nicht berechnet.")
    else:
        e_kwh = e_int_wh / 1000.0
        operativ = e_kwh * a.intensitaet                       # g
        if a.embodied_kg is not None:
            lebens_s = a.lebensdauer_jahre * 365 * 24 * 3600
            m_g = a.embodied_kg * 1000 * (t_int_s / lebens_s)
        else:
            m_g = 0.0
        sci = operativ + m_g
        p(f"- E = {e_kwh:.3e} kWh, I = {a.intensitaet:g} gCO2e/kWh  ->  E*I = {operativ * 1000:.3f} mg CO2e")
        if a.embodied_kg is None:
            p("- M nicht angegeben (`--embodied-kg`): SCI enthält nur den Betriebsanteil.")
        else:
            p(f"- M = {a.embodied_kg:g} kg * (Dauer {t_int_s:.1f} s / Lebensdauer {a.lebensdauer_jahre:g} a) "
              f"= {m_g * 1000:.3f} mg CO2e (ANNAHME Herstellungsfußabdruck)")
        p(f"- **SCI = {sci * 1000:.3f} mg CO2e pro Interaktion**")

        # Alltagsvergleich: 5-W-LED
        led_s = e_int_wh * 3600 / 5.0
        p(f"\n## Alltagsvergleich\n\nEine Antwort kostet so viel Energie wie eine 5-W-LED-Lampe in "
          f"**{led_s:.1f} Sekunden**.")

    if a.md:
        Path(a.md).write_text("\n".join(ausgabe) + "\n", encoding="utf-8")
        print(f"\n-> gespeichert: {a.md}")


if __name__ == "__main__":
    main()
