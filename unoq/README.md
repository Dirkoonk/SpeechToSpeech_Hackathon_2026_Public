# Arduino UNO Q – Lingo

- `sketch/lingo_mcu/` – MCU (Zephyr): Modulinos → Bridge-Ereignisse, LED-Zustände
- `python/` – MPU (Debian, nativ im venv): Zustandsautomat, BLE, Speech-Pipeline, UI

Schnittstellen: `../docs/ARCHITECTURE.md`. Englische Bauanleitung: `../docs/DOCUMENTATION.md`.

## Aufbau (Entscheidung Q0.1: Hybrid)

| Teil | Wie |
|---|---|
| Sketch | `arduino-cli` auf dem Board, Hochladen **lokal** (`remoteocd` ohne Netzwerkadresse – im WLAN sind weitere UNO Qs) |
| Python | nativ auf Debian im venv `~/lingo-venv` (Audio, BLE, Display direkt erreichbar, kein Container) |
| Bridge | `arduino-router-bridge` (MPL-2.0) direkt auf `/run/arduino-router.sock` |
| Code aufs Board | `git clone` auf dem Board nach `~/lingo`, oder vom Laptop `UNOQ=arduino@<IP> bash unoq/sync.sh` |

## Einmalige Einrichtung

```bash
sudo apt install -y python3-pip python3-tk libportaudio2
sudo usermod -aG arduino-router arduino          # Bridge-Socket ohne sudo
python3 -m venv --without-pip --system-site-packages ~/lingo-venv
~/lingo-venv/bin/python -m pip install vosk piper-tts bleak msgpack arduino-router-bridge
arduino-cli lib install Arduino_Modulino
```

Modelle unter `/home/arduino/models` (keine Umlaute im Pfad):

| Datei | Quelle |
|---|---|
| `llm/Qwen3.5-0.8B-Q8_0.gguf` | huggingface.co/unsloth/Qwen3.5-0.8B-GGUF |
| `vosk/vosk-model-small-de-0.15`, `vosk/vosk-model-small-en-us-0.15` | alphacephei.com (Mirror: huggingface.co/Derur/vosk-models) |
| `piper/de_DE-thorsten-low.onnx(.json)`, `piper/en_US-lessac-low.onnx(.json)` | huggingface.co/rhasspy/piper-voices |

llama.cpp: Release `b11988` (`llama-b11988-bin-ubuntu-arm64.tar.gz`) unter `~/opt/llama`.

## Start

Vom Laptop (Git Bash, im Repo):

```bash
bash unoq/sync.sh              # Code aufs Board
bash unoq/mcu.sh upload        # Sketch kompilieren + hochladen (bash unoq/mcu.sh compile = nur prüfen)
```

Auf dem Board (SSH oder Terminal am Display):

```bash
bash ~/lingo/unoq/start_lingo.sh            # App im Vollbild (Demo, ohne Logs)
bash ~/lingo/unoq/start_lingo.sh --debug    # mit Logs + CSV ~/lingo_results.csv (Benchmarks)
```

Einzeltests (im Ordner `~/lingo/unoq/python`, Python = `~/lingo-venv/bin/python`):

| Test | Befehl |
|---|---|
| Bridge-Ereignisse (Q1.1–Q1.3) | `python bridge_test.py` · `--state 1` (A blinkt) · `--cycle` |
| Pipeline ohne Mikro (Q1.4) | `python s2s_pipeline.py --selftest [--play]` |
| Pipeline mit Mikro | `python s2s_pipeline.py --mode chat` · `--mode translate --src de` |
| BLE (Q2.7) | `python ble_central.py` · `--alert 1` |
| Wissen / Wetter | `python knowledge.py "wann sind die besuchszeiten"` · `python weather.py` |

USB-Audio wählen: `LINGO_MIC=plughw:1,0 LINGO_SPK=plughw:1,0` (Kartennummer aus `arecord -l` / `aplay -l`).

### Ablauf und Bedienung

1. **Standby** – „Hey Lingo“ (Nicla) oder Taste A lang → **Anfahrt**.
2. Person vor dem Sensor (< 0,5 m) → **Auswahl**: drei Kacheln, alle Tasten-LEDs an.
   Hier läuft **kein** Sprachmodell und das Mikro ist aus.
3. Knob drehen = Kachel wählen, **Knob drücken = Programm starten**. Erst jetzt lädt das
   Programm, was es braucht (Anzeige „wird gestartet …“, ca. 15 s):
   - **Assistent:** llama-server + Vosk DE. Hört und spricht **nur Deutsch**.
   - **Dolmetscher:** llama-server + Vosk DE/EN + Piper EN.
   - **Übersicht:** nichts.
4. **Knob drücken im Programm = Beenden** → zurück zur Auswahl. Modelle werden entladen,
   der llama-server beendet (~1,1 GB frei).
5. **Tasten wirken immer**, in jedem Zustand, und unterbrechen laufende Antworten sofort:
   A kurz = Ladestation, B = Material holen, C = Pflegekraft rufen.

Die Oberfläche skaliert automatisch auf das angeschlossene Display (7" 1024×600 bis Full HD).
Statuszeile: MCU rot = Modulinos nicht erreichbar. „Nicla aus“ / „WLAN aus“ (grau) sind kein
Fehler: ohne Nicla ruft man mit Taste A lang, WLAN wird nur für das Wetter gebraucht.

### Dolmetscher (Chat + Mikro kippen)

- Das **Modulino Movement** sitzt am Mikrofon. Die MCU misst die Neigung über die Schwerkraft
  (Beschleunigungssensor; Ruhelage = erste Sekunde nach dem Start) und sendet `ev_tilt(-1|+1)`
  bei Seitenwechsel (> ca. 20°, 250 ms gehalten).
- **Links = Deutsch, rechts = Englisch.** Falls andersherum montiert: `LINGO_TILT_INVERT=1`.
- Jede Äußerung erscheint als Chat-Blase (Original + Übersetzung) und die Übersetzung wird
  gesprochen. Verlauf nur im RAM, wird beim Beenden des Dolmetschers gelöscht (Privatsphäre).
- Ein gemeinsamer Übersetzungs-Prompt für beide Richtungen bleibt im LLM-Cache → Sprecherwechsel
  kostet keine Zusatzzeit (~5 s pro Satz, Phrasenbibliothek < 1 s).
- Fallback ohne Movement: Knob im Dolmetscher drehen = Sprecher wechseln (links DE, rechts EN).

### Bedienung ohne Modulinos/Nicla (Tastatur am UNO Q)

`[` / `]` = Mikro links / rechts gekippt.

`r` Ruf · `d` Dock · `a`/`A` Taste A kurz/lang · `b` · `c` · `←`/`→` Knob · `Enter` Knob drücken
(Auswahl: starten, Programm: beenden) · `p` Person da/weg. Im Debug-Modus zusätzlich per SSH: `echo r > /tmp/lingo_cmd`,
`echo "say Wann sind die Besuchszeiten?" > /tmp/lingo_cmd` (Frage als Text).

## Messwerte (UNO Q 4 GB, 4× A53 ohne dotprod)

| Was | Wert |
|---|---|
| llama-bench Qwen3.5-0.8B Q8_0 | Prompt 11,5 Tok/s · Ausgabe 5,5 Tok/s |
| Chat „Besuchszeiten“ kalt (langer Prompt, ohne Cache) | 16,4 s bis erster Ton |
| Chat warm (fester System-Prompt im Cache) | 8,6 s bis erster Ton |
| Übersetzung DE→EN (LLM) / Phrasenbibliothek | 5,1 s / < 1 s |
| Programmstart Assistent / Dolmetscher (llama-server + Modelle + Prompt) | ~14 s / ~17 s |
| Programm beenden (Modelle entladen, llama-server stoppen) | ~1,5 s |
| RAM llama-server / Python-App im Programm | ~1,1 GB / 0,41 GB (Assistent) · 0,67 GB (Dolmetscher) |
| RAM verfügbar: Standby/Auswahl · Assistent · Dolmetscher | ~2,7 GB · ~2,3 GB · ~2,1 GB |

RAM-Tipps: App Lab (Chromium/WebKit, ~1,2 GB) für die Demo schließen. `--cache-ram 128`
in `start_llama.sh` ist wichtig (Standard wären 8 GiB Prompt-Cache).
Modelle und llama-server starten erst mit dem ersten Programm und bleiben dann bis zur Rückkehr
zur Ladestation im RAM (Wechsel zwischen Programmen ohne erneutes Laden).

**RAM gegen Antwortzeit (Assistent):** Der llama-server hat 9 Slots (1 Dolmetscher + 8 Themen).
Nach dem Programmstart liest das Modell im Hintergrund die Daten jedes Themas einmal vor
(8 Themen, ~75 s, Anzeige „Wissen wird vorgeladen 3/8“). Danach liest es pro Frage nur noch die
Frage (~20 statt ~100–190 Token). Kosten: llama-server ~1,56 GB statt ~1,1 GB.

| Frage (Zeit bis zum ersten Ton) | vorher | mit vorgeladenem Thema |
|---|---|---|
| Besuchszeiten | 8,6 s | 4,8 s |
| Mittagessen | 8,8 s | 5,6 s |
| Wetter morgen | 15–17 s | 6,4 s |
| Cafeteria | 8,2 s | 6,1 s |
| Witz (wird ohne Sprachmodell gesprochen) | 8,8 s | 1,2 s |
| Dieselbe Frage oder derselbe Satz noch einmal (Antwort-Cache) | wie beim ersten Mal | < 0,1 s |
| Feste Ansage (Taste B/C) erzeugen, ab dem zweiten Mal | 0,9 s | 0 s |

**CPU ist knapper als RAM:** Das LLM nutzt alle 4 Kerne. Alles, was nebenher rechnet, macht
Antworten deutlich langsamer (gemessen: 9 s → 32 s). Deshalb: Bluetooth-Manager-Fenster
(`blueman-manager`) nie offen lassen (Dauersuche = ~1 Kern), BLE-Suche der App läuft nur in
kurzen Abschnitten und pausiert während eines Programms, Animationen max. 6 Bilder/s.

**USB-Mikro:** Meldet die App „Mikrofon nicht gefunden“, ist das USB-Mikro vom Bus gefallen
(`arecord -l` zeigt keine Karte „Set“) → neu einstecken, die App macht von selbst weiter.
