# Lingo – offline interpreter and ward assistant robot for hospitals

A robot head on the **Arduino UNO Q** that listens, understands and answers in German and English without a single word leaving the device. A **Nicla Voice** worn by the nurse detects the custom wake word "Hey Lingo" on its Syntiant NDP120 and calls the robot via Bluetooth LE.

Built during the Arduino Speech to Speech Hackathon 2026 (Nuremberg).

## What it does

- **Called by wake word:** "Hey Lingo" at the Nicla Voice → the robot wakes up ("On my way to room 12", simulated drive).
- **Assistant:** answers questions about the ward (visiting hours, meals, directions, weather) only from stored data.
- **Interpreter German ⇄ English:** tilt the microphone to choose the speaker; Lingo speaks the translation and shows both texts.
- **Buttons that always work:** back to the charging station, fetch supplies, call the nurse (the Nicla blinks red).
- **Fully local:** Vosk (speech recognition), Qwen3.5-0.8B on llama.cpp (answers, translation), Piper (speech output). The only internet access is a weather forecast from Open-Meteo.

## Hardware

Arduino UNO Q (4 GB), Arduino Nicla Voice, Modulino Distance, Knob, Buttons and Movement, USB microphone and speaker, 7" HDMI display, USB-C hub, power bank.

## Repository

| Path | Content |
|---|---|
| `docs/DOCUMENTATION.md` | Build and setup guide (start here) |
| `docs/ARCHITECTURE.md` | Architecture, Bluetooth and bridge interfaces, states |
| `docs/BENCHMARKS.md` | Measurements, energy model, SCI |
| `nicla_voice/` | Nicla Voice sketch (wake word → Bluetooth LE) |
| `unoq/sketch/` | UNO Q microcontroller sketch (Modulinos → bridge events) |
| `unoq/python/` | UNO Q Linux side: state machine, speech pipeline, Bluetooth, UI |
| `data/` | Fictitious ward data, phrase library, jokes, test sentences |
| `benchmarks/` | Recording, benchmark and evaluation scripts, results |

Not included: the trained wake-word model (Edge Impulse terms), language/speech models (download links in the documentation) and audio recordings.

## Limits

Lingo is not a replacement for certified interpreters (medical consent, briefings). The small language model can make mistakes, so the text is always shown. All ward data and test sentences are fictitious.

## License

GPL-3.0 (see `LICENSE`). Third-party components keep their own licenses.
