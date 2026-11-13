"""BLE-Central (Q2.7) mit bleak: verbindet sich mit `Lingo-Nicla`, abonniert `cmd`,
schreibt `alert`. Läuft als Thread mit eigener asyncio-Schleife, verbindet neu
bei Abbruch. Vertrag: docs/SCHNITTSTELLEN.md, Abschnitt 1.

Test allein:  python ble_central.py            (gibt Befehle aus)
              python ble_central.py --alert 1  (sendet PFLEGE_GERUFEN)
Optional:     LINGO_NICLA_ADDR=AA:BB:..  feste Adresse, falls mehrere gleichnamige Geräte
"""
import asyncio
import os
import subprocess
import sys
import threading
import time

NAME = "Lingo-Nicla"
SERVICE_UUID = "78961615-5fa8-4e98-96c7-174601080225"
CMD_UUID = "d1cec2b3-ee87-4515-8017-93d8832234be"
ALERT_UUID = "13b5e598-e7be-410f-b31b-47dbcefbce89"
HEARTBEAT = 0x7F
DEBOUNCE_S = 1.0
SCAN_S = 4.0         # so lange suchen ...
SCAN_PAUSE_S = 11.0  # ... dann Pause: Dauersuche kostet auf dem UNO Q über einen CPU-Kern
                     # (BlueZ/D-Bus melden jedes Gerät im Raum), der dem Sprachmodell fehlt

DEBUG = os.environ.get("DEBUG") == "1"


def log(*a):
    if DEBUG:
        print("[ble]", *a, file=sys.stderr, flush=True)


class NiclaLink:
    def __init__(self, on_cmd, on_status=None, address=None):
        """on_cmd(cmd_id) bei RUF/DOCK/...; on_status(connected: bool) bei Änderung.
        Beide laufen im BLE-Thread: kurz halten (z. B. in eine Queue legen)."""
        self.on_cmd = on_cmd
        self.on_status = on_status or (lambda c: None)
        self.address = (address or os.environ.get("LINGO_NICLA_ADDR") or "").upper() or None
        self.connected = False
        self.last_heartbeat = 0.0
        self._last_cmd = (None, 0.0)
        self._loop = None
        self._client = None
        self._stop = threading.Event()
        self.quiet = False  # True = nicht suchen (z. B. während das Sprachmodell rechnet)

    def start(self):
        threading.Thread(target=lambda: asyncio.run(self._main()), name="ble", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def alive(self, timeout=12.0):
        """Verbunden und Heartbeat (alle 5 s) nicht älter als `timeout`."""
        return self.connected and time.monotonic() - self.last_heartbeat < timeout

    def send_alert(self, alert_id):
        """Thread-sicher, blockiert nicht. Ohne Verbindung wird der Alarm verworfen."""
        if not (self._loop and self._client and self.connected):
            log("alert verworfen (nicht verbunden)", alert_id)
            return False
        asyncio.run_coroutine_threadsafe(self._write(alert_id), self._loop)
        return True

    async def _write(self, alert_id):
        try:
            await self._client.write_gatt_char(ALERT_UUID, bytes([alert_id & 0xFF]), response=True)
            log("alert gesendet", alert_id)
        except Exception as e:  # noqa: BLE001 – BLE-Fehler dürfen den Roboter nie stoppen
            log("alert fehlgeschlagen", e)

    def _match(self, dev, adv):
        if self.address:
            return dev.address.upper() == self.address
        name = adv.local_name or dev.name
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        # exakter Name; wenn die Service-UUID im Advertising steht, muss sie passen
        return name == NAME and (not uuids or SERVICE_UUID in uuids)

    def _notify(self, _char, data: bytearray):
        if not data:
            return
        v = data[0]
        now = time.monotonic()
        if v == 0x00:
            return
        if v == HEARTBEAT:
            self.last_heartbeat = now
            return
        last_v, last_t = self._last_cmd
        if v == last_v and now - last_t < DEBOUNCE_S:
            return  # Entprellung 1 s
        self._last_cmd = (v, now)
        log("cmd", hex(v))
        self.on_cmd(v)

    @staticmethod
    def _drop_stale():
        """Verwaiste Verbindung trennen: Wurde die App beendet, hält BlueZ den Nicla manchmal
        weiter verbunden. Dann sendet er kein Advertising mehr und wird nie wieder gefunden."""
        try:
            out = subprocess.run(["bluetoothctl", "devices", "Connected"], capture_output=True, text=True,
                                 timeout=5).stdout
            for line in out.splitlines():
                parts = line.split(" ", 2)
                if len(parts) == 3 and parts[2].strip() == NAME:
                    log("trenne verwaiste Verbindung", parts[1])
                    subprocess.run(["bluetoothctl", "disconnect", parts[1]], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            log("bluetoothctl nicht nutzbar:", e)

    async def _main(self):
        from bleak import BleakClient, BleakScanner
        self._loop = asyncio.get_running_loop()
        await self._loop.run_in_executor(None, self._drop_stale)
        while not self._stop.is_set():
            try:
                if self.quiet:
                    await asyncio.sleep(1.0)
                    continue
                dev = await BleakScanner.find_device_by_filter(self._match, timeout=SCAN_S)
                if dev is None:
                    await asyncio.sleep(SCAN_PAUSE_S)
                    continue
                log("verbinde", dev.address)
                async with BleakClient(dev) as client:
                    self._client = client
                    await client.start_notify(CMD_UUID, self._notify)
                    self.connected = True
                    self.last_heartbeat = time.monotonic()
                    self.on_status(True)
                    while client.is_connected and not self._stop.is_set():
                        await asyncio.sleep(0.5)
            except Exception as e:  # noqa: BLE001
                log("Fehler", type(e).__name__, e)
            finally:
                if self.connected:
                    self.connected = False
                    self.on_status(False)
                self._client = None
            await asyncio.sleep(2.0)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--alert", type=int)
    a = ap.parse_args()
    names = {0x01: "RUF", 0x02: "DOCK", 0x03: "RUF_FALLBACK"}
    link = NiclaLink(on_cmd=lambda c: print(time.strftime("%H:%M:%S"), "cmd", hex(c), names.get(c, "?"), flush=True),
                     on_status=lambda c: print("verbunden" if c else "getrennt", flush=True)).start()
    try:
        sent = False
        while True:
            time.sleep(1)
            if a.alert and link.connected and not sent:
                time.sleep(1)
                print("sende alert", a.alert, link.send_alert(a.alert), flush=True)
                sent = True
    except KeyboardInterrupt:
        pass
