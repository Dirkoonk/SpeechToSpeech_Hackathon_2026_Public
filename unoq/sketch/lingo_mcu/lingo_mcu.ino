// Lingo · MCU des UNO Q (STM32U585, Zephyr)
// Liest Modulino Distance, Knob und Buttons (~50 Hz) und schickt nur Ereignisse
// über die Bridge an Python (docs/SCHNITTSTELLEN.md, Abschnitt 2).
// Grundsatz: Die MCU wartet NIE auf Python – nur Bridge.notify, keine Bridge.call.
// Lizenz: GPL-3.0

#include <Arduino_RouterBridge.h>
#include <Modulino.h>

#define LINGO_DEBUG 0  // 1 = Ausgaben im Monitor (nur zum Testen, Demo: 0)
#if LINGO_DEBUG
#define DBG(...) Monitor.println(__VA_ARGS__)
#else
#define DBG(...)
#endif

// ---- Parameter (Vertrag) ---------------------------------------------------
const uint32_t LOOP_MS = 20;            // ~50 Hz
const uint32_t DIST_MIN_INTERVAL = 200; // ev_distance max. 5x/s
const int DIST_MIN_DELTA = 20;          // ... und nur bei Änderung > 20 mm
const int PRESENCE_ON_MM = 500;         // an bei < 500 mm ...
const uint32_t PRESENCE_ON_MS = 300;    // ... für 300 ms
const int PRESENCE_OFF_MM = 650;        // aus bei > 650 mm ...
const uint32_t PRESENCE_OFF_MS = 1000;  // ... für 1 s
const int DIST_NONE_MM = 1300;          // kein Ziel im Messbereich = "weit weg"
const uint32_t DIST_INVALID_HOLD_MS = 400; // ungültige Messung erst nach 400 ms als "weit weg"
const uint32_t LONG_PRESS_MS = 1000;    // Taste lang > 1 s
const uint32_t BLINK_MS = 400;          // Blinktakt Taste A in ANFAHRT
const uint32_t RETRY_MS = 5000;         // fehlende Modulinos neu suchen

// Zustände (gemeinsame IDs)
enum { ST_STANDBY = 0, ST_ANFAHRT = 1, ST_ASSISTENT = 2, ST_DOLMETSCHER = 3, ST_UEBERSICHT = 4 };

// Kippen des Mikrofons (Modulino Movement): Wer spricht im Dolmetscher?
const float TILT_ON_G = 0.35f;          // ~20° Neigung gegenüber der Ruhelage
const uint32_t TILT_HOLD_MS = 250;      // so lange halten, bevor es zählt
const uint32_t TILT_CAL_MS = 1000;      // Ruhelage nach dem Start 1 s mitteln

ModulinoDistance distance;
ModulinoKnob knob;
ModulinoButtons buttons;
ModulinoMovement movement;
bool hasDist = false, hasKnob = false, hasButtons = false, hasMovement = false;

// Von Python gesetzt (Bridge-Thread) – nur Variablen schreiben, I2C macht loop()
volatile int reqState = ST_STANDBY;
volatile int reqMask = 0;
volatile bool reqOverride = false;  // true = set_leds hat Vorrang bis zum nächsten set_state
volatile bool ledsDirty = true;
volatile bool resendReq = true;     // aktuellen Abstand/Präsenz einmal neu senden

bool set_state(int state) {
  if (state != reqState) resendReq = true;  // nur bei echtem Zustandswechsel
  reqState = state;
  reqOverride = false;
  ledsDirty = true;
  return true;
}

bool set_leds(int mask) {
  reqMask = mask & 0x07;
  reqOverride = true;
  ledsDirty = true;
  return true;
}

// ---- Distance / Präsenz -----------------------------------------------------
int lastSentMm = -1000;
uint32_t lastDistSent = 0;
bool present = false;
uint32_t condSince = 0;  // seit wann die Gegenbedingung zur aktuellen Präsenz gilt

int lastValidMm = DIST_NONE_MM;
uint32_t lastValidT = 0;

void handleDistance(uint32_t now) {
  int mm;
  if (distance.available()) {
    mm = (int)distance.get();
    if (mm > DIST_NONE_MM) mm = DIST_NONE_MM;
    lastValidMm = mm;
    lastValidT = now;
  } else if (now - lastValidT < DIST_INVALID_HOLD_MS) {
    mm = lastValidMm;  // einzelne ungültige Messungen überbrücken
  } else {
    mm = DIST_NONE_MM;
  }

  if (resendReq) {
    // Zustandswechsel: Python sofort synchronisieren (z. B. Person steht schon da)
    resendReq = false;
    Bridge.notify("ev_presence", present);
    Bridge.notify("ev_distance", mm);
    lastSentMm = mm;
    lastDistSent = now;
    return;
  }
  if (abs(mm - lastSentMm) > DIST_MIN_DELTA && now - lastDistSent >= DIST_MIN_INTERVAL) {
    Bridge.notify("ev_distance", mm);
    lastSentMm = mm;
    lastDistSent = now;
  }

  bool toggleCond = present ? (mm > PRESENCE_OFF_MM) : (mm < PRESENCE_ON_MM);
  uint32_t holdMs = present ? PRESENCE_OFF_MS : PRESENCE_ON_MS;
  if (!toggleCond) {
    condSince = 0;
  } else if (condSince == 0) {
    condSince = now;
  } else if (now - condSince >= holdMs) {
    present = !present;
    condSince = 0;
    Bridge.notify("ev_presence", present);
    DBG(present ? "presence on" : "presence off");
  }
}

// ---- Knob -------------------------------------------------------------------
int16_t knobLast = 0;
bool knobWasPressed = false;

void handleKnob() {
  int16_t pos = knob.get();  // liest auch den Taster-Zustand
  bool pressed = knob.isPressed();
  int16_t diff = pos - knobLast;
  if (diff > 50 || diff < -50) diff = 0;  // Lesefehler/Überlauf ignorieren
  while (diff > 0) { Bridge.notify("ev_knob", 1); diff--; }
  while (diff < 0) { Bridge.notify("ev_knob", -1); diff++; }
  knobLast = pos;
  if (pressed && !knobWasPressed) Bridge.notify("ev_knob_press");
  knobWasPressed = pressed;
}

// ---- Buttons ----------------------------------------------------------------
bool btnDown[3] = {false, false, false};
uint32_t btnSince[3] = {0, 0, 0};

void handleButtons(uint32_t now) {
  if (!buttons.update()) return;  // nur bei Änderung
  for (int i = 0; i < 3; i++) {
    bool down = buttons.isPressed(i) == HIGH;
    if (down && !btnDown[i]) {
      btnSince[i] = now;
    } else if (!down && btnDown[i]) {
      bool isLong = (now - btnSince[i]) > LONG_PRESS_MS;
      Bridge.notify("ev_button", i, isLong);  // beim Loslassen
      DBG(isLong ? "button long" : "button short");
    }
    btnDown[i] = down;
  }
}

// ---- Kippen (Movement) ------------------------------------------------------
// Ruhelage = mittlere Schwerkraftrichtung in der ersten Sekunde. Danach zählt die
// stärkere Abweichung auf der X- oder Y-Achse; ihr Vorzeichen ist die Seite.
// ev_tilt(side): -1 / +1, nur bei Seitenwechsel. Welche Seite welche Sprache ist,
// entscheidet Python (umschaltbar, falls der Sensor andersherum montiert ist).
float calX = 0, calY = 0;
int calN = 0;
uint32_t calStart = 0;
int tiltSide = 0, tiltCand = 0;
uint32_t tiltCandSince = 0;
const uint32_t ANGLE_MIN_INTERVAL = 150;  // ev_tilt_angle max. ~7x/s ...
const int ANGLE_MIN_DELTA = 2;            // ... und nur bei Änderung >= 2°
int lastAngleSent = 999;
uint32_t lastAngleT = 0;

void handleMovement(uint32_t now) {
  if (!movement.update()) return;
  float x = movement.getX(), y = movement.getY();
  if (calStart == 0) calStart = now;
  if (now - calStart < TILT_CAL_MS) {
    calX += x; calY += y; calN++;
    return;
  }
  float dx = x - (calN ? calX / calN : 0);
  float dy = y - (calN ? calY / calN : 0);
  float d = fabsf(dx) > fabsf(dy) ? dx : dy;

  // Winkel für die Anzeige im Dolmetscher (nur dort, gedrosselt)
  if (reqState == ST_DOLMETSCHER) {
    if (now - lastAngleT >= ANGLE_MIN_INTERVAL) {
      float dc = d > 1.0f ? 1.0f : (d < -1.0f ? -1.0f : d);
      int deg = (int)lroundf(asinf(dc) * 57.2958f);
      if (abs(deg - lastAngleSent) >= ANGLE_MIN_DELTA) {
        Bridge.notify("ev_tilt_angle", deg);
        lastAngleSent = deg;
        lastAngleT = now;
      }
    }
  } else {
    lastAngleSent = 999;  // beim nächsten Betreten sofort senden
  }

  int cand = d > TILT_ON_G ? 1 : (d < -TILT_ON_G ? -1 : 0);
  if (cand == 0 || cand == tiltSide) { tiltCand = 0; return; }
  if (cand != tiltCand) { tiltCand = cand; tiltCandSince = now; return; }
  if (now - tiltCandSince >= TILT_HOLD_MS) {
    tiltSide = cand;
    tiltCand = 0;
    Bridge.notify("ev_tilt", tiltSide);
    DBG(tiltSide > 0 ? "tilt +1" : "tilt -1");
  }
}

// ---- LEDs -------------------------------------------------------------------
int shownMask = -1;

void updateLeds(uint32_t now) {
  int mask;
  if (reqOverride) {
    mask = reqMask;
  } else {
    switch (reqState) {
      case ST_ANFAHRT:     mask = ((now / BLINK_MS) % 2) ? 0b001 : 0; break;
      case ST_ASSISTENT:   mask = 0b001; break;
      case ST_DOLMETSCHER: mask = 0b010; break;
      case ST_UEBERSICHT:  mask = 0b100; break;
      default:             mask = 0; break;
    }
  }
  if (mask != shownMask || ledsDirty) {
    buttons.setLeds(mask & 1, mask & 2, mask & 4);
    shownMask = mask;
    ledsDirty = false;
  }
}

// ---- Setup / Loop -----------------------------------------------------------
uint32_t lastRetry = 0;

void beginModules() {
  if (!hasDist) hasDist = distance.begin();
  if (!hasKnob) { hasKnob = knob.begin(); if (hasKnob) knobLast = knob.get(); }
  if (!hasButtons) { hasButtons = buttons.begin(); if (hasButtons) ledsDirty = true; }
  if (!hasMovement) hasMovement = movement.begin();
}

void setup() {
  Bridge.begin();
#if LINGO_DEBUG
  Monitor.begin();
#endif
  Bridge.provide("set_state", set_state);
  Bridge.provide("set_leds", set_leds);
  Modulino.begin();  // UNO Q: Qwiic-Stecker = Wire1 (Bibliotheks-Standard)
  beginModules();
  DBG(String("dist=") + hasDist + " knob=" + hasKnob + " buttons=" + hasButtons);
}

void loop() {
  static uint32_t next = 0;
  uint32_t now = millis();
  if ((int32_t)(now - next) < 0) { delay(1); return; }
  next = now + LOOP_MS;

  if (now - lastRetry > RETRY_MS && !(hasDist && hasKnob && hasButtons && hasMovement)) {
    lastRetry = now;
    beginModules();
  }
  if (hasMovement) handleMovement(now);
  if (hasDist) handleDistance(now);
  if (hasKnob) handleKnob();
  if (hasButtons) { handleButtons(now); updateLeds(now); }
}
