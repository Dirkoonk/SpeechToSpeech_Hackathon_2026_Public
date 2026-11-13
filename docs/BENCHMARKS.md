# Lingo – Benchmarks, energy and SCI

## Method

- Hardware: Arduino UNO Q (4 GB). Pipeline: Vosk → phrase library or Qwen3.5-0.8B (llama.cpp) → Piper.
- 10 fixed test sentences (5 assistant questions in German, 3 German and 2 English sentences for the interpreter), spoken by the team, no patient data.
- Each sentence 3 times after warm-up (median), plus a cold-start measurement and a comparison phrase library vs. language model.
- **Times are measured. Energy and CO₂ are modelled**, because no power meter was available:
  `P = P_idle + (P_peak − P_idle) × CPU utilization`, with CPU utilization = CPU time / (wall time × 4 cores),
  `E = Σ over stages (speech recognition, language model, speech output) of P × time`.
- Raw data: `../benchmarks/results.csv` (40 runs; column names are German, see below).

## Results (warm, median of 3 runs)

| Sentence | Mode | Path | Total time | Energy (modelled) |
|---|---|---|---|---|
| Wann sind die Besuchszeiten? | Assistant | Language model | 14.5 s | 12.4 mWh |
| Wann gibt es Mittagessen? | Assistant | Language model | 11.1 s | 9.1 mWh |
| Wo ist die Cafeteria? | Assistant | Language model | 10.5 s | 8.6 mWh |
| Wie wird das Wetter morgen? | Assistant | Language model | 19.6 s | 17.1 mWh |
| Erzähl mir einen Witz. | Assistant | Language model | 11.4 s | 9.1 mWh |
| Haben Sie Schmerzen? | Interpreter | Phrase library | 2.4 s | 0.8 mWh |
| Bitte nicht essen und trinken bis zur Untersuchung. | Interpreter | Language model | 10.6 s | 8.2 mWh |
| Ihre Tochter hat angerufen und kommt heute Abend vorbei. | Interpreter | Language model | 8.2 s | 6.0 mWh |
| Yes, my stomach hurts since yesterday evening. | Interpreter | Language model | 10.0 s | 6.7 mWh |
| Can I call my family from here? | Interpreter | Language model | 9.1 s | 6.5 mWh |

## Optimization staircase

| Step | Time until fully spoken | Energy (modelled) |
|---|---|---|
| Cold start (first request after start) | 18.2 s | 11.0 mWh |
| Warmed up | 10.0 s | 6.7 mWh |
| Short sentence via language model | 5.4 s | 3.9 mWh |
| Same sentence via phrase library | 2.4 s | 0.8 mWh |

After this benchmark run the assistant was optimized further (one language-model cache slot per topic). Time to first spoken word, measured directly on the pipeline: visiting hours 8.6 s → 4.8 s, weather 15–17 s → 6.4 s, joke 8.8 s → 1.2 s. These values are not part of the raw data file.

## Wake word vs. always-on listening (24 h, modelled)

| Variant | Energy per 24 h |
|---|---|
| UNO Q listens permanently itself (speech recognition always running, ≈ 1.34 W) | 32.5 Wh |
| Nicla Voice listens, UNO Q only wakes up on a call | 12.5 Wh |
| **Factor** | **≈ 2.6× less energy** |

## SCI per answer (Green Software Foundation, modelled)

`SCI = (E × I + M) per R`, R = one spoken interaction (question → spoken answer)

| Quantity | Value |
|---|---|
| E, energy per answer (median, warm) | 8.4 mWh at 10.6 s |
| E × I | 3.0 mg CO₂e |
| M, embodied share | 2.2 mg CO₂e |
| **SCI** | **5.3 mg CO₂e per answer** |

Everyday comparison: one answer ≈ a 5 W LED bulb for 6 seconds.

## Assumptions

| Assumption | Value | Origin |
|---|---|---|
| UNO Q idle / full load | 0.5 W / 3.5 W | Reference values, linear load model |
| Nicla Voice while listening | 5 mW | Assumption, not measured |
| Conversations per day | 40 | Assumption |
| Carbon intensity, German electricity mix | 363 g CO₂/kWh | German Environment Agency (UBA), 2024, preliminary |
| Manufacturing UNO Q + Nicla | 20 kg CO₂e | Assumption without source |
| Lifetime | 3 years | Assumption |

## Known limitations of this run

- Two sentences were recognized imprecisely ("bis zur" → "bis zu", "from here" → "from yeah"); the second standard sentence therefore missed the phrase library.
- The run alternated between assistant and interpreter, which discarded the language-model prompt cache; assistant times are therefore rather too high.

## Columns of `benchmarks/results.csv`

`test_id` sentence ID · `variante` mode (assistent/dolmetscher) · `uebersetzer` path (phrasen = phrase library, llm = language model, - = assistant) · `warm` 0 = cold, 1 = warm · `wiederholung` run · `stt_*` speech recognition · `llm_*` language model · `tts_*` speech output · `*_wall_s` wall time in s · `*_cpu_s` CPU time in s · `gesamt_wall_s` total time · `notiz` recognized text and time to first audio.
