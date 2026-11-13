# Auswertung Benchmarks (Mediane, Energie **modelliert**)

Quelle: results.csv, 40 Zeilen. Lastmodell: P_idle=0.5 W, P_peak=3.5 W, 4 Kerne.

## Warm, Standardweg (Phrasenbibliothek wenn Treffer)

| Test | Variante | Übersetzer | n | STT s | LLM s | TTS s | Gesamt s | Energie mWh |
|---|---|---|---|---|---|---|---|---|
| T01 | assistent | - | 3 | 2.29 | 10.99 | 1.24 | 14.50 | 12.404 |
| T02 | assistent | - | 3 | 2.20 | 7.61 | 1.18 | 11.05 | 9.073 |
| T03 | assistent | - | 3 | 2.26 | 6.87 | 1.37 | 10.52 | 8.555 |
| T04 | assistent | - | 3 | 2.11 | 15.98 | 1.39 | 19.59 | 17.074 |
| T05 | assistent | - | 3 | 2.43 | 7.68 | 1.19 | 11.36 | 9.142 |
| T06 | dolmetscher | phrasen | 3 | 1.95 | 0.00 | 0.41 | 2.35 | 0.795 |
| T07 | dolmetscher | llm | 3 | 2.82 | 6.73 | 0.99 | 10.62 | 8.243 |
| T08 | dolmetscher | llm | 3 | 2.94 | 4.42 | 0.80 | 8.22 | 5.962 |
| T09 | dolmetscher | llm | 3 | 4.51 | 4.62 | 0.85 | 10.02 | 6.728 |
| T10 | dolmetscher | llm | 3 | 3.63 | 4.74 | 0.78 | 9.15 | 6.495 |

## Kalt vs. aufgewärmt

| Test | Kalt Gesamt s | Warm Gesamt s | Faktor |
|---|---|---|---|
| T09 | 18.24 | 10.02 | 1.8x |

## Phrasenbibliothek vs. LLM-Übersetzung (warm)

| Test | Phrasen Gesamt s | LLM Gesamt s | Faktor | Phrasen mWh | LLM mWh |
|---|---|---|---|---|---|
| T06 | 2.35 | 5.43 | 2.3x | 0.795 | 3.904 |

## Dauerlauschen vs. Wake-Word auf dem Nicla (24 h, modelliert)

- Energie pro Interaktion (Median, warm): **8.40 mWh** bei 10.6 s Gesamtdauer
- STT-CPU-Auslastung (Median): 28 %  ->  Dauerlauschen mit Vosk ca. 1.34 W
- ANNAHME 40 Interaktionen/Tag; ANNAHME Nicla 5.0 mW
- A  Dauerlauschen auf dem UNO Q: **32.5 Wh / 24 h**
- B  Wake-Word auf dem Nicla:    **12.5 Wh / 24 h**
- **Faktor A/B: 2.6x weniger Energie** mit Nicla

## SCI pro Interaktion (modelliert)

- E = 8.399e-06 kWh, I = 363 gCO2e/kWh  ->  E*I = 3.049 mg CO2e
- M = 20 kg * (Dauer 10.6 s / Lebensdauer 3 a) = 2.235 mg CO2e (ANNAHME Herstellungsfußabdruck)
- **SCI = 5.284 mg CO2e pro Interaktion**

## Alltagsvergleich

Eine Antwort kostet so viel Energie wie eine 5-W-LED-Lampe in **6.0 Sekunden**.
