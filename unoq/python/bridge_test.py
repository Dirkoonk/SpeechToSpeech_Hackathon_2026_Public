"""Testskript Bridge (Abnahme Q1.1–Q1.3).

Gibt alle MCU-Ereignisse aus und schaltet optional einen Zustand.
  python bridge_test.py             # Ereignisse ausgeben
  python bridge_test.py --state 1   # set_state(1) -> Taste A blinkt
  python bridge_test.py --leds 5    # set_leds(0b101) -> A und C an
  python bridge_test.py --cycle     # alle Zustände 0..4 je 3 s durchschalten
"""
import argparse
import time

from lingo_bridge import EVENTS, LingoBridge


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", type=int)
    ap.add_argument("--leds", type=int)
    ap.add_argument("--cycle", action="store_true")
    a = ap.parse_args()

    br = LingoBridge()
    for name in EVENTS:
        br.on(name, lambda *p, n=name: print(f"{time.strftime('%H:%M:%S')} {n} {list(p)}", flush=True))
    br.start()
    print("Bridge verbunden, warte auf Ereignisse (Strg+C beendet) ...", flush=True)

    if a.state is not None:
        br.set_state(a.state)
    if a.leds is not None:
        br.set_leds(a.leds)
    try:
        if a.cycle:
            for s in range(5):
                print("set_state", s, flush=True)
                br.set_state(s)
                time.sleep(3)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        br.stop()


if __name__ == "__main__":
    main()
