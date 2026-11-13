"""Zustandsautomat (I.1–I.4) nach docs/ARCHITEKTUR.md und docs/SCHNITTSTELLEN.md.

STANDBY -> ANFAHRT   (BLE 0x01/0x03 oder Taste A lang)
ANFAHRT -> AUSWAHL   (ev_presence(true); ohne Person nach Ankunft spätestens nach 20 s)
AUSWAHL: Knob drehen = Programm wählen, Knob drücken = Programm starten.
         Erst jetzt werden die Modelle des Programms geladen.
Programm (ASSISTENT | DOLMETSCHER | UEBERSICHT): Knob drücken = Programm beenden
         -> zurück zur AUSWAHL. Geladene Modelle bleiben im RAM (schneller Wiedereinstieg),
         freigegeben wird alles erst bei der Rückkehr zur Ladestation.
         Dolmetscher: Mikro kippen (ev_tilt) wählt den Sprecher, Knob drehen ebenso (Fallback).
Immer (in jedem Zustand): Taste A kurz = zur Ladestation, Taste B = Material holen,
         Taste C = Pflegekraft rufen. Sie unterbrechen jede laufende Ansage/Antwort.
Zurück zur Ladestation geht es nur auf Befehl (Taste A kurz oder BLE 0x02), nie von allein.
(Früher: nach 60 s ohne Person. Bei Bedarf wieder einschalten mit LINGO_NO_PERSON_S=60.)

Reine Logik ohne Hardware. Wirkung über ein `actions`-Objekt mit den Methoden:
  set_state(state), say(text, lang, urgent=False), alert(alert_id),
  set_mode(mode, src, release=False), ui(**felder)
"""
import os
import time

# IDs 0–4 = gemeinsame Zustände (SCHNITTSTELLEN.md, LED-Muster der MCU).
# AUSWAHL ist nur in Python: MCU bekommt set_state(0) + set_leds(0b111) (alle Tasten an = „bitte wählen“).
STANDBY, ANFAHRT, ASSISTENT, DOLMETSCHER, UEBERSICHT, AUSWAHL = range(6)
NAMES = {STANDBY: "Standby", ANFAHRT: "Anfahrt", ASSISTENT: "Assistent",
         DOLMETSCHER: "Dolmetscher", UEBERSICHT: "Übersicht", AUSWAHL: "Auswahl"}
TILES = [ASSISTENT, DOLMETSCHER, UEBERSICHT]   # = Programme
NAH = set(TILES) | {AUSWAHL}
MODE = {ASSISTENT: "assistent", DOLMETSCHER: "dolmetscher", UEBERSICHT: None}

# BLE-Befehle (Nicla -> UNO Q)
CMD_RUF, CMD_DOCK, CMD_RUF_FALLBACK, CMD_HEARTBEAT = 0x01, 0x02, 0x03, 0x7F
# BLE-Alarme (UNO Q -> Nicla)
ALERT_PFLEGE, ALERT_ANGEKOMMEN = 0x01, 0x02
# Tasten
BTN_A, BTN_B, BTN_C = 0, 1, 2

ZIMMER = "Zimmer 12"
SIM_START_M = 5.0        # simulierter Abstand zu Beginn der Anfahrt
SIM_SPEED_MPS = 0.35     # simulierte Fahrgeschwindigkeit
REAL_TAKEOVER_MM = 1300  # echter Distance-Wert übernimmt unter 1,3 m
# Automatische Rückkehr nach so vielen Sekunden ohne Person. 0 = aus (Standard): Lingo bleibt,
# bis Taste A gedrückt wird.
NO_PERSON_S = float(os.environ.get("LINGO_NO_PERSON_S", "0") or 0)
PRESENT_AT_START_S = 2   # Person steht schon da, wenn die Anfahrt beginnt
ARRIVED_WAIT_S = 20      # an der Tür (1,3 m) ohne Person: nach 20 s trotzdem begrüßen
TILT_ON_DEG = 20         # ab dieser Neigung zählt das Kippen (wie TILT_ON_G auf der MCU)

TEXT_COME_CLOSER = "Ich bin da. Bitte kommen Sie etwas näher."
TEXT_GREETING = "Hallo, ich bin Lingo. Bitte wählen Sie mit dem Drehknopf und drücken Sie ihn zum Starten."
TEXT_AUSWAHL = "Bitte wählen Sie ein Programm."
TEXT_DOCK = "Ich fahre zurück zur Ladestation."
TEXT_MATERIAL = "Ich hole Material: Medikamente, Ladekabel, Decke oder Wasser."
TEXT_PFLEGE = "Ich rufe die Pflegekraft. Sie kommt gleich."
TEXT_ASSISTENT = "Assistent. Fragen Sie mich etwas."
TEXT_DOLMETSCHER = "Dolmetscher. Mikrofon nach links kippen für Deutsch."
TEXT_DOLMETSCHER_EN = "Tilt the microphone to the right for English."

# Mikro kippen (ev_tilt): Seite -1 = links = Deutsch, +1 = rechts = Englisch.
# Ist der Movement-Sensor andersherum montiert: LINGO_TILT_INVERT=1
TILT_SIGN = -1 if os.environ.get("LINGO_TILT_INVERT") == "1" else 1


def tilt_lang(side):
    return "de" if side * TILT_SIGN < 0 else "en"


class StateMachine:
    def __init__(self, actions, clock=time.monotonic):
        self.a = actions
        self.clock = clock
        self.state = STANDBY
        self.tile = 0             # hervorgehobene Kachel (Index in TILES)
        self.direction = "de"     # Dolmetscher: Sprache des Sprechers
        self.present = False
        self.present_since = None
        self.absent_since = None
        self.distance_mm = None   # letzter echter Wert
        self.anfahrt_t0 = None
        self.via_nicla = False
        self.at_door_t = None     # Zeitpunkt, an dem die Simulation 1,3 m erreicht hat

    # ---- Ereignisse --------------------------------------------------------
    def on_ble(self, cmd):
        if cmd in (CMD_RUF, CMD_RUF_FALLBACK):
            self.call(via_nicla=True)
        elif cmd == CMD_DOCK:
            self.dock()

    def on_button(self, btn, long):
        """Tasten wirken immer, in jedem Zustand, und unterbrechen laufende Ansagen."""
        if btn == BTN_A:
            if long:
                self.call(via_nicla=False)  # Fallback: simulierter Ruf
            else:
                self.dock()
        elif btn == BTN_B:
            self.a.ui(info=TEXT_MATERIAL)
            self.a.say(TEXT_MATERIAL, "de", urgent=True)
        elif btn == BTN_C:
            self.a.alert(ALERT_PFLEGE)
            self.a.ui(info=TEXT_PFLEGE)
            self.a.say(TEXT_PFLEGE, "de", urgent=True)

    def on_knob(self, step):
        if self.state == AUSWAHL:
            self.tile = (self.tile + (1 if step > 0 else -1)) % len(TILES)
            self.a.ui(tile=self.tile)
        elif self.state == DOLMETSCHER:
            # Fallback ohne Kippen: Drehen wechselt den Sprecher
            self._set_direction("en" if step > 0 else "de")

    def on_knob_press(self):
        if self.state == AUSWAHL:
            self._start(TILES[self.tile])
        elif self.state in TILES:
            self.leave()

    def select(self, i):
        """Touch/Maus auf eine Kachel der Auswahl = wählen + starten."""
        if self.state == AUSWAHL:
            self.tile = i
            self._start(TILES[i])

    def on_tilt(self, side):
        """Mikro gekippt: Sprecher/Sprache wechseln (nur im Dolmetscher)."""
        if self.state == DOLMETSCHER:
            self._set_direction(tilt_lang(side))

    def on_presence(self, present):
        now = self.clock()
        self.present = bool(present)
        self.present_since = now if present else None
        self.absent_since = None if present else now
        if present and self.state == ANFAHRT:
            self._arrive()

    def on_distance(self, mm):
        self.distance_mm = mm
        if self.state != ANFAHRT or mm < REAL_TAKEOVER_MM:
            self.a.ui(distance_m=mm / 1000, distance_real=True)

    def tick(self):
        """Regelmäßig aufrufen (~10 Hz)."""
        now = self.clock()
        if self.state == ANFAHRT:
            if self.distance_mm is None or self.distance_mm >= REAL_TAKEOVER_MM:
                sim = max(REAL_TAKEOVER_MM / 1000, SIM_START_M - SIM_SPEED_MPS * (now - self.anfahrt_t0))
                self.a.ui(distance_m=sim, distance_real=False)
                if sim <= REAL_TAKEOVER_MM / 1000:
                    if self.at_door_t is None:
                        # angekommen, aber niemand vor dem Sensor: freundlich bitten
                        self.at_door_t = now
                        self.a.ui(info=TEXT_COME_CLOSER)
                        self.a.say(TEXT_COME_CLOSER, "de")
                    elif now - self.at_door_t >= ARRIVED_WAIT_S:
                        self._arrive()  # Demo darf nie hängen bleiben
                        return
            if self.present and self.present_since and now - self.present_since >= PRESENT_AT_START_S \
                    and now - self.anfahrt_t0 >= PRESENT_AT_START_S:
                self._arrive()
        elif NO_PERSON_S > 0 and self.state in NAH and not self.present and self.absent_since \
                and now - self.absent_since >= NO_PERSON_S:
            self.dock()

    # ---- Übergänge ---------------------------------------------------------
    def call(self, via_nicla):
        if self.state != STANDBY:
            return
        self.state = ANFAHRT
        self.via_nicla = via_nicla
        self.anfahrt_t0 = self.clock()
        self.at_door_t = None
        self.a.set_state(ANFAHRT)
        self.a.set_mode(None, None)
        self.a.ui(state=ANFAHRT, info=f"Bin unterwegs zu {ZIMMER}", handover="nicla" if via_nicla else "taste",
                  distance_m=SIM_START_M, distance_real=False)

    def _arrive(self):
        self.a.alert(ALERT_ANGEKOMMEN)
        real = self.distance_mm / 1000 if self.distance_mm is not None else None
        self.a.ui(handover="mikro", distance_m=real, distance_real=True)
        self.tile = 0
        self._to_auswahl(TEXT_GREETING)

    def _to_auswahl(self, text):
        self.state = AUSWAHL
        self.absent_since = None if self.present else self.clock()
        self.a.set_state(AUSWAHL)
        self.a.set_mode(None, None)   # Programm beenden (Mikro aus); Modelle bleiben bis zur Ladestation
        self.a.ui(state=AUSWAHL, tile=self.tile, info="")
        self.a.say(text, "de")

    def leave(self):
        """Programm beenden -> Auswahl."""
        if self.state in TILES:
            self._to_auswahl(TEXT_AUSWAHL)

    def dock(self):
        if self.state == STANDBY:
            return
        self.state = STANDBY
        self.a.set_mode(None, None, release=True)  # zurück zur Ladestation: alle Modelle freigeben
        self.a.set_state(STANDBY)
        self.a.say(TEXT_DOCK, "de", urgent=True)
        self.a.ui(state=STANDBY, info=TEXT_DOCK, handover=None)

    def _start(self, state):
        """Programm starten (erst jetzt werden seine Modelle geladen)."""
        self.state = state
        self.tile = TILES.index(state)
        self.absent_since = None if self.present else self.clock()
        if state == DOLMETSCHER:
            self.direction = "de"
        self.a.set_state(state)
        self.a.ui(state=state, tile=self.tile, direction=self.direction, info="")
        if state == ASSISTENT:
            self.a.say(TEXT_ASSISTENT, "de")
        elif state == DOLMETSCHER:
            self.a.say(TEXT_DOLMETSCHER, "de")
            self.a.say(TEXT_DOLMETSCHER_EN, "en")
        # Assistent hört und spricht nur Deutsch; der Dolmetscher beginnt mit Deutsch
        self.a.set_mode(MODE[state], "de")

    def _set_direction(self, lang):
        if lang == self.direction:
            return
        self.direction = lang
        self.a.ui(direction=lang)
        self.a.set_mode("dolmetscher", lang)  # Zuhören in der neuen Sprache neu starten
