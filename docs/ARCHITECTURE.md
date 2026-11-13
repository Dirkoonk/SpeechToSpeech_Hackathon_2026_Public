# Lingo – Architecture and interfaces

## Overview

```
 Nicla Voice (nurse's coat)  ──BLE: 1-byte command──►  Arduino UNO Q (robot head)
 NDP120 wake word            ◄──BLE: 1-byte alert───   ┌ Linux processor (QRB2210, Debian):
 battery powered                                       │   speech recognition, language model,
                                                       │   speech output, UI, state machine
                                                       │        ▲  Bridge (events only)
                                                       │        ▼
                                                       └ Microcontroller (STM32U585, Zephyr):
                                                           Modulino Distance · Knob · Buttons · Movement
                                                       USB: microphone · speaker    HDMI: display

 Outlook (not built): Arduino VENTUNO Q as the body, in the same local ward network
```

| Layer | Device | Principle | Job |
|---|---|---|---|
| 1 · Guardian | Nicla Voice | Energy | Wake word "Hey Lingo" on the Syntiant NDP120, sends 1 byte via BLE, shows alerts on its LED |
| 2 · Nervous system | UNO Q microcontroller | Safety, real time | Reads the Modulinos ~50 times per second, sends only events, drives the button LEDs, never waits for Linux |
| 3 · Speech brain | UNO Q Linux processor | Privacy | Vosk (speech → text), Qwen3.5-0.8B on llama.cpp (answers, translation), Piper (text → speech), Tkinter UI |

## Bluetooth LE: Nicla Voice ↔ UNO Q

The Nicla Voice is the peripheral (advertising name `Lingo-Nicla`), the UNO Q is the central (Python, `bleak`).

| Element | UUID | Direction | Properties |
|---|---|---|---|
| Service | `78961615-5fa8-4e98-96c7-174601080225` | – | – |
| `cmd` | `d1cec2b3-ee87-4515-8017-93d8832234be` | Nicla → UNO Q | Read, Notify, 1 byte |
| `alert` | `13b5e598-e7be-410f-b31b-47dbcefbce89` | UNO Q → Nicla | Write, 1 byte |

| `cmd` value | Meaning |
|---|---|
| `0x01` | CALL: wake word "Hey Lingo" detected → robot starts "on my way" |
| `0x7F` | HEARTBEAT every 5 s → connection indicator |
| `0x00` | none (the Nicla resets the value 200 ms after each event) |

| `alert` value | Meaning | Nicla LED |
|---|---|---|
| `0x01` | NURSE_CALLED (button C on the robot) | blinks red for 3 s |
| `0x02` | ARRIVED (person detected at the robot) | green for 1 s |

## Bridge: microcontroller ↔ Linux (UNO Q)

The microcontroller only sends events and never waits for an answer. Linux calls the microcontroller only for LED feedback.

| Event (MCU → Linux) | Parameter | When |
|---|---|---|
| `ev_distance` | distance in mm | max. 5× per second, only on change > 20 mm |
| `ev_presence` | present yes/no | on below 500 mm for 300 ms, off above 650 mm for 1 s |
| `ev_knob` | step +1 / −1 | per detent |
| `ev_knob_press` | – | knob pressed |
| `ev_button` | button A/B/C, long press yes/no | on release, long = held > 1 s |

| Function (Linux → MCU) | Effect |
|---|---|
| `set_state` | LED pattern of the buttons for the current state |
| `set_leds` | set button LEDs directly |

## States

```
          "Hey Lingo" (BLE 0x01) or button A long
 STANDBY ─────────────────────────────────────────► ON THE WAY (simulated)
    ▲                                                   │ person < 0.5 m
    │ button A short / BLE command                      ▼
    └──────────────────────────────────────────── SELECTION ──► ASSISTANT | INTERPRETER | OVERVIEW
```

## Privacy

- Audio stays in memory only and is discarded after the answer. No transcripts are stored (except in an explicit debug mode).
- The only internet access: weather forecast from Open-Meteo every 30 minutes, without an API key.
- The wake word is detected on the Nicla; only a 1-byte event leaves the device.
