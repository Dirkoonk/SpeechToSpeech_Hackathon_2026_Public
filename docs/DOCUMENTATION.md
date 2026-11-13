# Lingo – Build and setup guide

How to rebuild Lingo: an offline interpreter and ward assistant robot head on the Arduino UNO Q, called by a Nicla Voice with a custom wake word.

Architecture, Bluetooth and bridge interfaces: see `ARCHITECTURE.md`. Measurements: see `BENCHMARKS.md`.

## 1. Hardware

| Part | Qty | Note |
|---|---|---|
| Arduino UNO Q (4 GB) | 1 | Robot head |
| Arduino Nicla Voice | 1 | Wearable wake-word guardian |
| Modulino Distance, Knob, Buttons, Movement | 1 each | On the UNO Q microcontroller via Qwiic |
| Qwiic cables | 4 | Daisy chain of the four Modulinos |
| Elecrow 7" IPS HD display | 1 | Face and subtitles, HDMI |
| USB microphone, USB speaker | 1 each | Speech input and output |
| USB-C hub with external power supply | 1 | Connects microphone, speaker and display to the UNO Q |
| Power bank | 1 | Powers the Nicla Voice on the nurse's coat |

### Wiring

```
UNO Q Qwiic ── Distance ── Knob ── Buttons ── Movement      (4 Qwiic cables, I²C bus Wire1)
UNO Q USB-C ── hub (external power) ── USB microphone
                                    ├─ USB speaker
                                    └─ HDMI ── Elecrow 7" display
Nicla Voice ── USB ── power bank     (no cable to the robot, Bluetooth LE only)
```

Mount the Modulino Movement on the microphone: tilting the microphone left selects German, right selects English in interpreter mode.

## 2. Nicla Voice (wake word "Hey Lingo" → Bluetooth)

Sketch: `nicla_voice/lingo_nicla/lingo_nicla.ino` (Arduino IDE 2 or Arduino CLI).

1. Install the board package **Arduino Mbed OS Nicla Boards** (`arduino:mbed_nicla`, tested with 4.0.8) and the library **ArduinoBLE**. The `NDP` library comes with the board package.
2. **Put the NDP files into the Nicla's external flash** (once). The sketch loads at startup: `mcu_fw_120_v91.synpkg`, `dsp_firmware_v91.synpkg` (both in the board package under `libraries/NDP/extra/`) and the keyword model `ei_model.synpkg`.
   - Flash the example **NDP → Syntiant_upload_fw_ymodem**.
   - Build the `syntiant-uploader` tool from the example's `extra/` folder (Go: `go build`).
   - Send each file: `syntiant-uploader send -m "Y" -w "Y" -p <port> <file>.synpkg`. If the transfer stops with "Didn't get a nak when expected", repeat it; if the board hangs, unplug and replug it.
   - Check: send `L` over the serial port (115200 baud) to list the files.
3. Flash `lingo_nicla`. After power-on the Nicla needs about **30 s** to load the model; then it advertises as `Lingo-Nicla`.

**Your own wake word (Edge Impulse):** project type Arduino Nicla Voice; classes `Hey-Lingo` (we recorded it 253 times) and `z_openset` (noise, similar words, normal speech); window 968 ms at 16 kHz; processing block "Audio (Syntiant)" with feature extractor `log-bin (NDP120/200)`; learning block "Transfer Learning (Keyword Spotting)"; deployment "Syntiant NDP120 library" with "Find posterior parameters". From the export only `ei_model.synpkg` is needed. The model file is not in this repository (Edge Impulse terms). Without your own model, load the shipped demo keyword instead: in the sketch replace `NDP.load("ei_model.synpkg")` with the commented line for `alexa_334_NDP120_B0_v11_v91.synpkg`.

LED feedback on the Nicla: blue = wake word detected, red blinking 3 s = nurse called (button C on the robot), green 1 s = robot arrived.

## 3. Arduino UNO Q

### One-time setup on the board (Debian)

```bash
sudo apt install -y python3-pip python3-tk libportaudio2
sudo usermod -aG arduino-router arduino                 # bridge socket without sudo
python3 -m venv --without-pip --system-site-packages ~/lingo-venv
~/lingo-venv/bin/python -m pip install vosk piper-tts bleak msgpack arduino-router-bridge
arduino-cli lib install Arduino_Modulino
```

Copy the repository to `~/lingo` on the board: `git clone https://github.com/Dirkoonk/SpeechToSpeech_Hackathon_2026_Public ~/lingo`.

### Models (offline, under `/home/arduino/models`, no special characters in the path)

| File | Source |
|---|---|
| `llm/Qwen3.5-0.8B-Q8_0.gguf` | huggingface.co/unsloth/Qwen3.5-0.8B-GGUF |
| `vosk/vosk-model-small-de-0.15`, `vosk/vosk-model-small-en-us-0.15` | alphacephei.com/vosk/models |
| `piper/de_DE-thorsten-low.onnx(.json)`, `piper/en_US-lessac-low.onnx(.json)` | huggingface.co/rhasspy/piper-voices |

llama.cpp: release `b11988` (`llama-b11988-bin-ubuntu-arm64.tar.gz`) unpacked to `~/opt/llama`. The app starts and stops `llama-server` itself (`unoq/start_llama.sh`).

### Microcontroller sketch

`unoq/sketch/lingo_mcu/` reads the four Modulinos on `Wire1` about 50 times per second and sends only events over the bridge. Compile and upload on the board:

```bash
arduino-cli compile --fqbn arduino:zephyr:unoq --build-path /tmp/lingo_mcu_build ~/lingo/unoq/sketch/lingo_mcu
arduino-cli upload  --fqbn arduino:zephyr:unoq --input-dir /tmp/lingo_mcu_build
```

The sketch stays in flash and starts automatically after power-on.

### Start the app (after every reboot)

```bash
bash ~/lingo/unoq/start_lingo.sh            # full screen, demo mode, no logs
bash ~/lingo/unoq/start_lingo.sh --debug    # with logs and a CSV line per interaction
```

USB audio devices can be selected with `LINGO_MIC=plughw:1,0 LINGO_SPK=plughw:1,0` (card number from `arecord -l` / `aplay -l`).

Before a demo: close any Bluetooth window on the board's desktop (it slows down answers considerably), lay the microphone flat before starting, keep everyone further than 65 cm from the distance sensor.

## 4. Operation

| Control | Effect |
|---|---|
| "Hey Lingo" at the Nicla, or button A long | Call Lingo: "On my way to room 12" (simulated drive) |
| Person closer than 0.5 m | Selection with three tiles; the Nicla lights up green |
| Turn the knob / press the knob | Select a tile / start or end a program |
| Assistant | Ask about visiting hours, meals, directions, weather, quiet hours, or a joke (German) |
| Interpreter | Tilt the microphone left (German) or right (English) and speak; Lingo speaks the translation |
| Button A short | Back to the charging station (standby) |
| Button B | Fetch supplies (announcement) |
| Button C | Call the nurse; the Nicla blinks red |

The buttons work in every state and interrupt a running answer.

## 5. Test without the full setup

| Test | Command (on the UNO Q, in `~/lingo/unoq/python`, Python = `~/lingo-venv/bin/python`) |
|---|---|
| Bridge events | `python bridge_test.py` |
| Speech pipeline without microphone | `python s2s_pipeline.py --selftest` |
| Bluetooth to the Nicla | `python ble_central.py` (`--alert 1` = red, `--alert 2` = green) |
| Ward knowledge / weather | `python knowledge.py "wann sind die besuchszeiten"` · `python weather.py` |

Benchmarks: record the 10 test sentences with `benchmarks/record_testsaetze.py`, run `benchmarks/run_benchmark.py` on the UNO Q, evaluate with `benchmarks/auswertung.py`.

## 6. Privacy and limits

- Audio stays in memory and is discarded after each answer. The only internet access is the Open-Meteo weather forecast (no API key); without Wi-Fi the last forecast is used.
- Ward data, test sentences and phrases are fictitious. No patient data.
- Lingo is not a replacement for certified interpreters (medical consent, briefings). The small language model can make mistakes; the text is therefore always shown.

## License

Own code: GPL-3.0. Third-party components keep their licenses (Vosk Apache 2.0, llama.cpp MIT, Qwen3.5 Apache 2.0, Piper GPL-3.0, bleak MIT, ArduinoBLE LGPL-2.1, Arduino Router Bridge MPL-2.0).
