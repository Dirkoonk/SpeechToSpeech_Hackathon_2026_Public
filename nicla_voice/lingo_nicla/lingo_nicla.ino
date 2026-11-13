// Lingo · Nicla Voice
// Wake-Word auf dem NDP120 -> BLE-Peripheral "Lingo-Nicla" (cmd Notify, alert Write)
// Basis: NDP/AlexaDemo + BLExaDemo. Protokoll: docs/SCHNITTSTELLEN.md
#include "NDP.h"
#include <ArduinoBLE.h>

// --- Schnittstellen-Vertrag (nicht ändern ohne Absprache) ---
const char* SERVICE_UUID = "78961615-5fa8-4e98-96c7-174601080225";
const char* CMD_UUID     = "d1cec2b3-ee87-4515-8017-93d8832234be";
const char* ALERT_UUID   = "13b5e598-e7be-410f-b31b-47dbcefbce89";

const uint8_t CMD_NONE = 0x00, CMD_RUF = 0x01, CMD_DOCK = 0x02, CMD_FALLBACK = 0x03, CMD_HEARTBEAT = 0x7F;
const uint8_t ALERT_PFLEGE = 0x01, ALERT_ANGEKOMMEN = 0x02;

const unsigned long HEARTBEAT_MS = 5000;
const unsigned long CMD_RESET_MS = 200;

BLEService lingoService(SERVICE_UUID);
BLEByteCharacteristic cmdChar(CMD_UUID, BLERead | BLENotify);
BLEByteCharacteristic alertChar(ALERT_UUID, BLEWrite);

volatile bool keywordFlag = false;   // wird im NDP-Callback gesetzt, in loop() verarbeitet
unsigned long lastHeartbeat = 0;
unsigned long cmdSetAt = 0;
bool cmdActive = false;

void onKeyword(char* label) {
  keywordFlag = true;
}

void onNdpError() {
  while (1) {
    nicla::leds.begin();
    nicla::leds.setColor(red);
    delay(200);
    nicla::leds.setColor(off);
    delay(200);
    nicla::leds.end();
  }
}

void sendCmd(uint8_t id) {
  cmdChar.writeValue(id);
  cmdSetAt = millis();
  cmdActive = true;
}

// Blockierendes Blinken ist hier ok (kurz, loop() hat sonst nichts Zeitkritisches)
void ledBlink(RGBColors color, unsigned long totalMs, unsigned long periodMs) {
  nicla::leds.begin();
  unsigned long t0 = millis();
  while (millis() - t0 < totalMs) {
    nicla::leds.setColor(color);
    delay(periodMs / 2);
    nicla::leds.setColor(off);
    delay(periodMs / 2);
  }
  nicla::leds.end();
}

void handleAlert(uint8_t id) {
  if (id == ALERT_PFLEGE) {
    ledBlink(red, 3000, 400);
  } else if (id == ALERT_ANGEKOMMEN) {
    nicla::leds.begin();
    nicla::leds.setColor(green);
    delay(1000);
    nicla::leds.setColor(off);
    nicla::leds.end();
  }
}

void setup() {
  Serial.begin(115200);
  nicla::begin();
  nicla::disableLDO();
  Serial.println("setup: start");

  if (!BLE.begin()) {
    Serial.println("BLE start fehlgeschlagen");
    onNdpError();
  }
  Serial.println("setup: BLE ok");
  BLE.setLocalName("Lingo-Nicla");
  BLE.setDeviceName("Lingo-Nicla");
  BLE.setAdvertisedService(lingoService);
  lingoService.addCharacteristic(cmdChar);
  lingoService.addCharacteristic(alertChar);
  BLE.addService(lingoService);
  cmdChar.writeValue(CMD_NONE);

  NDP.onError(onNdpError);
  NDP.onMatch(onKeyword);
  Serial.println("setup: NDP.begin");
  NDP.begin("mcu_fw_120_v91.synpkg");
  NDP.load("dsp_firmware_v91.synpkg");
  // Eigenes Wake-Word "Hey Lingo" (Edge Impulse, Syntiant NDP120).
  // Fallback auf das Demo-Keyword "Alexa": die Zeile darunter statt dieser verwenden.
  Serial.println("setup: NDP.load Modell");
  NDP.load("ei_model.synpkg");
  // NDP.load("alexa_334_NDP120_B0_v11_v91.synpkg");
  Serial.println("setup: Mikrofon");
  NDP.turnOnMicrophone();
  NDP.interrupts();

  // Advertising erst nach dem NDP-Start (wie im Arduino-Beispiel BLExaDemo)
  BLE.advertise();
  Serial.println("Lingo-Nicla bereit");
}

void loop() {
  BLE.poll();
  unsigned long now = millis();

  if (keywordFlag) {
    keywordFlag = false;
    sendCmd(CMD_RUF);
    Serial.println("RUF");
    nicla::leds.begin();
    nicla::leds.setColor(blue);
    delay(200);
    nicla::leds.setColor(off);
    nicla::leds.end();
  }

  // Wert nach 200 ms zurück auf 0x00 (Vertrag)
  if (cmdActive && now - cmdSetAt >= CMD_RESET_MS) {
    cmdChar.writeValue(CMD_NONE);
    cmdActive = false;
  }

  // Heartbeat alle 5 s, nur wenn verbunden
  if (!cmdActive && BLE.connected() && now - lastHeartbeat >= HEARTBEAT_MS) {
    lastHeartbeat = now;
    sendCmd(CMD_HEARTBEAT);
  }

  // Advertising am Leben halten, solange niemand verbunden ist
  static unsigned long lastAdv = 0;
  if (!BLE.connected() && now - lastAdv >= 5000) {
    lastAdv = now;
    BLE.advertise();
    Serial.print("adv t=");
    Serial.println(now / 1000);
  }

  if (alertChar.written()) {
    handleAlert(alertChar.value());
  }

  delay(10);
}
