"""Dünne Hülle um die offizielle Router-Bridge (arduino-router-bridge, MPL-2.0).

Python läuft nativ auf Debian (nicht im App-Lab-Container) und spricht direkt
mit /run/arduino-router.sock. Benutzer `arduino` muss in der Gruppe
`arduino-router` sein.

Vertrag: docs/SCHNITTSTELLEN.md, Abschnitt 2.
- MCU -> Python: ev_distance, ev_presence, ev_knob, ev_knob_press, ev_button
- Python -> MCU: set_state, set_leds (nur per notify, nie blockierend)
"""
import os

from arduino.router_bridge import DEFAULT_ADDRESS, Bridge

# ev_tilt(side: -1|+1): Mikro gekippt (Modulino Movement) – Sprecherwahl im Dolmetscher
# ev_tilt_angle(deg: int): Kippwinkel für die Anzeige (nur im Zustand DOLMETSCHER)
EVENTS = ("ev_distance", "ev_presence", "ev_knob", "ev_knob_press", "ev_button", "ev_tilt", "ev_tilt_angle")


class LingoBridge:
    def __init__(self, address=None):
        self._br = Bridge(address or os.environ.get("APP_SOCKET", DEFAULT_ADDRESS))
        self._handlers = {}

    def on(self, event, handler):
        """Handler für ein MCU-Ereignis setzen. Läuft im Bridge-Thread: kurz halten
        (z. B. nur in eine Queue legen), keine Bridge-Aufrufe darin."""
        self._handlers[event] = handler

    def start(self, timeout=5.0):
        if not self._br.connect(timeout=timeout):
            self._br.disconnect()
            raise ConnectionError(f"Router nicht erreichbar: {self._br.address}")
        for name in EVENTS:
            self._br.provide(name, self._dispatcher(name))

    def _dispatcher(self, name):
        def handler(*params):
            h = self._handlers.get(name)
            if h:
                h(*params)
            return None
        return handler

    # Python -> MCU: immer notify (fire-and-forget), damit nichts blockiert
    def set_state(self, state: int):
        self._br.notify("set_state", int(state))

    def set_leds(self, mask: int):
        self._br.notify("set_leds", int(mask) & 0x07)

    def stop(self):
        self._br.disconnect()
