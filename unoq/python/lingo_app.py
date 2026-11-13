"""Lingo – Hauptprogramm auf dem UNO Q (ein Prozess).

Threads: Tk-Hauptthread (UI + Zustandsautomat), Sprache (STT/LLM/TTS),
BLE (bleak/asyncio), Bridge (Router), Wetter. Alle Ereignisse laufen über
eine Queue in den Hauptthread – nichts blockiert die UI oder die MCU.

Start:  bash ~/lingo/unoq/start_lingo.sh   (App auf :0; llama-server startet erst mit einem Programm)
Direkt: DISPLAY=:0 ~/lingo-venv/bin/python lingo_app.py [--window] [--debug] [--no-ble] [--save]

Tastatur (Test ohne Modulinos/Nicla): r = Ruf (wie BLE 0x01), d = Dock (BLE 0x02),
[ / ] = Mikro nach links / rechts gekippt (Dolmetscher: Deutsch / Englisch),
a = Taste A kurz, A = Taste A lang, b = Taste B, c = Taste C,
←/→ = Knob drehen, Enter = Knob drücken (Auswahl: starten, Programm: beenden),
p = Person da/weg umschalten,
Esc = Beenden (nur mit --window oder --debug)
"""
import argparse
import csv
import datetime
import os
import faulthandler
import queue
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk

import s2s_pipeline as s2s
import weather
from state_machine import AUSWAHL, STANDBY, TILT_SIGN, StateMachine
from ui import LingoUI

DEBUG = False
CSV_PATH = os.path.expanduser("~/lingo_results.csv")
CSV_COLS = ["timestamp", "test_id", "variante", "stt_engine", "uebersetzer", "warm", "wiederholung",
            "stt_wall_s", "stt_cpu_s", "llm_prompt_tokens", "llm_gen_tokens", "llm_wall_s", "llm_cpu_s",
            "tts_wall_s", "tts_cpu_s", "gesamt_wall_s", "energie_wh_modelliert", "energie_wh_gemessen", "notiz"]


def log(*a):
    if DEBUG:
        print("[app]", *a, file=sys.stderr, flush=True)


def ram_free_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        return None


def wlan_up():
    try:
        with open("/sys/class/net/wlan0/operstate") as f:
            return f.read().strip() == "up"
    except OSError:
        return False


def keep_display_on():
    """Display darf nie schwarz werden (Bildschirmschoner/DPMS aus). Läuft alle 60 s,
    weil XFCE die Einstellung z. B. nach dem Entsperren wieder zurücksetzt."""
    for args in (["s", "off"], ["s", "noblank"], ["-dpms"], ["s", "reset"]):
        try:
            subprocess.run(["xset", *args], timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return


LLAMA_SH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "start_llama.sh")

# Was jedes Programm braucht: (STT-Sprachen, TTS-Sprachen, LLM). Piper DE bleibt immer geladen
# (Ansagen der Tasten); alles andere wird erst beim Start des Programms geladen.
NEEDS = {
    "assistent": ({"de"}, {"de"}, True),
    "dolmetscher": ({"de", "en"}, {"de", "en"}, True),
}


def llama_start():
    """llama-server starten (wartet, bis er bereit ist). Läuft im Sprach-Thread."""
    try:
        return subprocess.run(["bash", LLAMA_SH], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def llama_stop():
    """llama-server beenden (~1,1 GB RAM frei). Modell bleibt im Seiten-Cache -> Neustart schnell."""
    subprocess.run(["pkill", "-x", "llama-server"], stderr=subprocess.DEVNULL)
    for _ in range(50):
        if subprocess.run(["pgrep", "-x", "llama-server"], stdout=subprocess.DEVNULL).returncode != 0:
            return
        time.sleep(0.1)
    subprocess.run(["pkill", "-9", "-x", "llama-server"], stderr=subprocess.DEVNULL)


class Announcer(threading.Thread):
    """Ansagen (Begrüßung, Tasten A/B/C, Programmstart) – eigener Thread, damit sie
    immer sofort kommen, auch während ein Programm lädt oder eine Antwort läuft."""

    def __init__(self, worker):
        super().__init__(name="ansage", daemon=True)
        self.w = worker
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False

    def say(self, text, lang, urgent=False):
        if urgent:
            # Taste: alles Laufende abbrechen (Ansage, Antwort, Zuhören) und sofort sprechen
            while True:
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    break
            self.cancel.set()
            self.w.pl.cancel.set()
        self.busy = True
        self.w.interrupt.set()
        self.q.put((text, lang))

    def idle(self):
        return not self.busy and self.q.empty()

    def run(self):
        while True:
            text, lang = self.q.get()
            self.busy = True
            self.cancel.clear()
            self.w.interrupt.set()  # Mikro zu, solange Lingo spricht
            self.w.post({"speaking": True})
            try:
                self.w.pl.say(text, lang, cancel=self.cancel)
            except Exception as e:  # noqa: BLE001
                log("Fehler Ansage:", type(e).__name__, e)
            if self.q.empty():
                self.busy = False
                self.w.post({"speaking": False})


class SpeechWorker(threading.Thread):
    """Programme sauber getrennt: Ein Programm (assistent | dolmetscher) lädt seine Modelle
    erst, wenn es gestartet wird, und gibt sie beim Beenden wieder frei (inkl. llama-server).
    Im aktiven Programm wird dauerhaft zugehört. Meldet alles über `post(dict)` an die UI.

    Dolmetscher: jede Äußerung wird ein Chat-Eintrag (Sprache, Original, Übersetzung).
    Der Verlauf liegt nur im RAM und wird beim Beenden des Dolmetschers gelöscht.
    """

    CHAT_MAX = 30

    def __init__(self, pipeline, post):
        super().__init__(name="sprache", daemon=True)
        self.pl = pipeline
        self.post = post
        self.jobs = queue.Queue()
        self.target = None    # gewünschtes Programm (Hauptthread)
        self.prog = None      # geladenes Programm (Sprach-Thread)
        self.mode = None      # aktiv zuhören in diesem Programm (erst wenn fertig geladen)
        self.src = "de"
        self.interrupt = threading.Event()
        self.warm = False
        self._cached = None   # welcher System-Prompt liegt im LLM-Cache
        self.chat = []        # [{"src": "de"|"en", "orig": str, "trans": str}]
        self.ann = Announcer(self)
        self.loaded = set()   # Programme, deren Modelle im RAM liegen (bis zur Ladestation)
        self._busy = False    # gerade wird eine Frage beantwortet (Vorladen wartet solange)
        self._preload_stop = threading.Event()
        self._preload_thread = None

    def start(self):
        self.ann.start()
        super().start()

    # aus dem Hauptthread
    def say(self, text, lang="de", urgent=False):
        self.ann.say(text, lang, urgent)

    def set_mode(self, mode, src, release=False):
        """mode None = kein Programm aktiv (Mikro aus). release=True gibt zusätzlich alle Modelle
        und den llama-server frei (Rückkehr zur Ladestation)."""
        self.src = src or "de"
        self.interrupt.set()
        if mode != self.target or release:
            self.target = mode
            self.mode = None
            self.pl.cancel.set()  # laufende Antwort des alten Programms abbrechen
            self.jobs.put(("program", mode, release))

    def ask(self, text):
        """Nur Debug: Äußerung als Text statt per Mikro."""
        self.interrupt.set()
        self.jobs.put(("ask", text))

    # im Sprach-Thread
    def run(self):
        while True:
            try:
                job = self.jobs.get(timeout=0.1)
            except queue.Empty:
                job = None
            try:
                if job:
                    self._job(job)
                elif self.mode:
                    self._converse()
            except Exception as e:  # noqa: BLE001 – Sprache darf die App nie beenden
                log("Fehler Sprache:", type(e).__name__, e)
                self.post({"listening": False, "thinking": False})
                time.sleep(1)

    def _job(self, job):
        kind = job[0]
        if kind == "program":
            self._switch(job[1], job[2])
        elif kind == "base":
            self.pl.tts.warmup("de")  # Ansagen sofort bereit
        elif kind == "ask":
            if self.mode:
                self.pl.cancel.clear()
                self._process(self.mode, self.src, job[1], {})

    def _switch(self, mode, release=False):
        if mode != self.target:
            return  # veraltet, ein neuerer Wechsel steht schon in der Queue
        self.pl.cancel.clear()
        t = time.monotonic()
        if self.prog == "dolmetscher" and mode != "dolmetscher":
            self.chat.clear()  # Gesprächsverlauf nie aufheben (Privatsphäre)
            self.post({"chat": ()})
        self.prog = mode
        if not mode:
            # Programm beendet. Modelle bleiben geladen (schneller Wiedereinstieg), außer bei release.
            if release and self.loaded:
                self._preload_stop.set()
                for lang in ("de", "en"):
                    self.pl.stt.unload(lang)
                self.pl.tts.unload("en")
                llama_stop()
                self.loaded.clear()
                s2s.trim_memory()
                log(f"Modelle entladen, llama-server beendet ({time.monotonic() - t:.1f}s)")
            self.post({"loading": "", "preload": "",
                       "models": ", ".join(sorted(self.loaded)) + " geladen" if self.loaded else ""})
            return
        if mode not in self.loaded:
            stt_need, tts_need, _ = NEEDS[mode]
            self.post({"loading": "wird gestartet …"})
            if not self.pl.llm.health():
                if not llama_start():
                    log("llama-server startet nicht")
                    self.post({"loading": "Sprachmodell startet nicht"})
                    return
            for lang in sorted(stt_need):
                self.pl.stt.model(lang)
            for lang in sorted(tts_need):
                if not self.pl.tts.loaded(lang):
                    self.pl.tts.warmup(lang)
            if mode == "dolmetscher":
                self.pl.llm.warmup(s2s.SYS_TRANSLATE, slot=s2s.SLOT["dolmetscher"])
            self.loaded.add(mode)
            self.warm = True
            log(f"Programm {mode} bereit in {time.monotonic() - t:.1f}s")
        self.post({"loading": "", "models": ", ".join(sorted(self.loaded)) + " geladen"})
        if mode == "assistent" and not (self._preload_thread and self._preload_thread.is_alive()):
            self._preload_stop.clear()
            self._preload_thread = threading.Thread(target=self._preload_topics, name="vorladen", daemon=True)
            self._preload_thread.start()
        if self.target == mode:
            self.mode = mode

    def _preload_topics(self):
        """Hintergrund: Das Sprachmodell liest die Daten jedes Themas einmal vorab in dessen Slot.
        Danach muss es bei einer Frage nur noch die Frage lesen (Antwort ~4 s statt 8–20 s).
        Der Assistent ist schon währenddessen nutzbar; noch nicht geladene Themen sind nur langsamer."""
        import knowledge
        t = time.monotonic()
        topics = knowledge.topics()
        for i, (name, data) in enumerate(topics, 1):
            while self._busy and not self._preload_stop.is_set():
                time.sleep(0.2)  # eine laufende Antwort hat Vorrang
            if self._preload_stop.is_set():
                return
            self.post({"preload": f"Wissen wird vorgeladen {i}/{len(topics)}"})
            self.pl.llm.warmup(s2s.sys_chat(data), slot=s2s.SLOT[name])
        if not self._preload_stop.is_set():
            self.post({"preload": ""})
            log(f"{len(topics)} Themen vorgeladen in {time.monotonic() - t:.1f}s")

    def _converse(self):
        mode, src = self.mode, self.src
        self.interrupt.clear()
        while not self.ann.idle():  # nicht zuhören, während Lingo etwas ansagt
            if not self.jobs.empty() or self.mode != mode:
                return
            time.sleep(0.1)
        self.interrupt.clear()
        self.pl.cancel.clear()
        self.post({"listening": True, "partial": ""})
        text, st = self.pl.stt.listen(src, stop=self.interrupt, save=self.pl.save,
                                      on_partial=lambda p: self.post({"partial": p}))
        self.post({"listening": False, "partial": ""})
        if st.get("mic_error") and not self.interrupt.is_set():
            # USB-Mikro abgezogen oder hängt: sichtbar melden und weiter versuchen
            log("Mikrofon nicht verfügbar")
            self.post({"info": "Mikrofon nicht gefunden – bitte USB-Mikrofon prüfen"})
            time.sleep(2)
            return
        if not text or self.interrupt.is_set() or self.mode != mode or self.src != src:
            return
        self._process(mode, src, text, st)

    def _post_chat(self):
        self.post({"chat": tuple((e["src"], e["orig"], e["trans"]) for e in self.chat)})

    def _process(self, mode, src, text, st):
        t0 = time.monotonic()
        state = {"speaking": False}
        log(f"Gehört [{mode}/{src}]:", text)
        self._busy = True
        try:
            self._answer(mode, src, text, st, t0, state)
        finally:
            self._busy = False

    def _answer(self, mode, src, text, st, t0, state):

        def started():
            if not state["speaking"]:
                state["speaking"] = True
                self.post({"thinking": False, "speaking": True})

        if mode == "assistent":
            import knowledge
            self.post({"sub_user": text, "sub_bot": "", "thinking": True})

            def on_text(t):
                started()
                self.post({"sub_bot": t})

            topic, data = knowledge.topic(text)
            answer, st2 = self.pl.chat(text, data, on_text=on_text, topic=topic)
            uebersetzer = "-"
            self.post({"sub_bot": answer})
        else:
            dst = "en" if src == "de" else "de"
            entry = {"src": src, "orig": text, "trans": ""}
            self.chat.append(entry)
            del self.chat[:-self.CHAT_MAX]
            self.post({"thinking": True})
            self._post_chat()

            def on_text(t):
                started()
                entry["trans"] = t
                self._post_chat()

            answer, st2 = self.pl.translate(text, src, dst, on_text=on_text)
            entry["trans"] = answer
            self._post_chat()
            uebersetzer = st2.pop("uebersetzer", "llm")
        total = time.monotonic() - t0 + st.get("stt_wall_s", 0)
        first = st2.get("first_audio_s")
        if self.pl.cancel.is_set():
            self.post({"thinking": False, "speaking": False})
            log("Antwort abgebrochen")
            return
        log(f"Antwort ({first or total:.1f}s):", answer)
        self.post({"thinking": False, "speaking": False, "last_answer_s": (first or total)})
        if DEBUG:
            self._csv(mode, uebersetzer, {**st, **st2}, total)

    def _csv(self, mode, uebersetzer, st, total):
        new = not os.path.exists(CSV_PATH)
        row = {k: "" for k in CSV_COLS}
        row.update({k: st.get(k, "") for k in CSV_COLS if k in st})
        row.update(timestamp=datetime.datetime.now().isoformat(timespec="seconds"), variante=mode,
                   stt_engine="vosk", uebersetzer=uebersetzer, warm=int(self.warm),
                   gesamt_wall_s=round(total, 3), notiz=f"first_audio_s={st.get('first_audio_s')}")
        with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLS)
            if new:
                w.writeheader()
            w.writerow(row)


class App:
    def __init__(self, args):
        self.args = args
        self.q = queue.Queue()
        self.root = tk.Tk()
        self.view = LingoUI(self.root, on_tile=self._on_tile, fullscreen=not args.window)
        self.pl = s2s.Pipeline(save=args.save)
        self.speech = SpeechWorker(self.pl, lambda d: self.q.put(("view", d)))
        self.sm = StateMachine(self)
        self.bridge = None
        self.ble = None
        self._next_slow = 0.0
        self._fake_present = False
        self._next_display = 0.0

    # ---- Aktionen für den Zustandsautomaten --------------------------------
    def set_state(self, state, periodic=False):
        """Zustand an die MCU (LED-Muster). AUSWAHL kennt die MCU nicht: Zustand 0 + alle
        Tasten-LEDs an (set_leds). Beim periodischen Auffrischen dort nur set_leds senden."""
        if not self.bridge:
            return
        try:
            if state == AUSWAHL:
                if not periodic:
                    self.bridge.set_state(STANDBY)
                self.bridge.set_leds(0b111)
            else:
                self.bridge.set_state(state)
        except Exception as e:  # noqa: BLE001
            log("set_state fehlgeschlagen", e)

    def say(self, text, lang, urgent=False):
        self.speech.say(text, lang, urgent)

    def alert(self, alert_id):
        if self.ble:
            self.ble.send_alert(alert_id)

    def set_mode(self, mode, src, release=False):
        self.speech.set_mode(mode, src, release)

    def ui(self, **kw):
        self.view.update(**kw)

    # ---- Start --------------------------------------------------------------
    def start(self):
        self._start_bridge()
        if not self.args.no_ble:
            from ble_central import NiclaLink
            self.ble = NiclaLink(on_cmd=lambda c: self.q.put(("ble", c)),
                                 on_status=lambda s: self.q.put(("view", {"nicla": s}))).start()
        weather.start_background()
        self.speech.start()
        llama_stop()                      # kein Sprachmodell im RAM, solange kein Programm läuft
        self.speech.jobs.put(("base",))   # nur die deutsche Stimme für Ansagen vorladen
        self.set_state(0)
        self._bind_keys()
        if DEBUG:
            threading.Thread(target=self._debug_fifo, name="fifo", daemon=True).start()
        self.root.after(50, self._poll)
        self.root.mainloop()

    def _start_bridge(self):
        try:
            from lingo_bridge import LingoBridge
            br = LingoBridge()
            br.on("ev_distance", lambda mm: self.q.put(("distance", mm)))
            br.on("ev_presence", lambda p: self.q.put(("presence", p)))
            br.on("ev_knob", lambda step: self.q.put(("knob", step)))
            br.on("ev_knob_press", lambda: self.q.put(("knob_press",)))
            br.on("ev_button", lambda i, lg: self.q.put(("button", i, lg)))
            br.on("ev_tilt", lambda side: self.q.put(("tilt", side)))
            br.on("ev_tilt_angle", lambda deg: self.q.put(("tilt_angle", deg)))
            br.start()
            self.bridge = br
            self.view.update(mcu=True)
        except Exception as e:  # noqa: BLE001 – ohne MCU weiter (Tastatur-Fallback)
            log("Bridge nicht verfügbar:", e)
            self.view.update(mcu=False)

    def _bind_keys(self):
        k = self.root.bind
        k("r", lambda e: self.q.put(("ble", 0x01)))
        k("d", lambda e: self.q.put(("ble", 0x02)))
        k("a", lambda e: self.q.put(("button", 0, False)))
        k("A", lambda e: self.q.put(("button", 0, True)))
        k("b", lambda e: self.q.put(("button", 1, False)))
        k("c", lambda e: self.q.put(("button", 2, False)))
        k("<Left>", lambda e: self.q.put(("knob", -1)))
        k("<Right>", lambda e: self.q.put(("knob", 1)))
        k("<Return>", lambda e: self.q.put(("knob_press",)))
        k("p", lambda e: self._toggle_presence())
        k("<bracketleft>", lambda e: self.q.put(("tilt", -1)))   # [ = Mikro links
        k("<bracketright>", lambda e: self.q.put(("tilt", 1)))   # ] = Mikro rechts
        if self.args.window or self.args.debug:
            k("<Escape>", lambda e: self.root.destroy())

    FIFO_CMDS = {"r": ("ble", 0x01), "d": ("ble", 0x02), "a": ("button", 0, False), "A": ("button", 0, True),
                 "b": ("button", 1, False), "c": ("button", 2, False), "left": ("knob", -1),
                 "right": ("knob", 1), "enter": ("knob_press",), "p1": ("presence", True),
                 "p0": ("presence", False), "tl": ("tilt", -1), "tr": ("tilt", 1)}
    # Debug: "angle 25" setzt den angezeigten Kippwinkel

    def _debug_fifo(self, path="/tmp/lingo_cmd"):
        """Nur Debug: Ereignisse per  echo r > /tmp/lingo_cmd  auslösen (Test ohne Tastatur)."""
        if not os.path.exists(path):
            os.mkfifo(path)
        while True:
            with open(path) as f:
                for line in f:
                    cmd = line.strip()
                    if cmd.startswith("angle "):
                        self.q.put(("tilt_angle", int(cmd.split()[1])))
                    elif cmd.startswith("dist "):
                        self.q.put(("distance", int(cmd.split()[1])))
                    elif cmd.startswith("say "):
                        self.speech.ask(cmd[4:])
                    elif cmd in self.FIFO_CMDS:
                        self.q.put(self.FIFO_CMDS[cmd])

    def _toggle_presence(self):
        self._fake_present = not self._fake_present
        self.q.put(("presence", self._fake_present))

    def _on_tile(self, i):
        """Maus/Touch: Kachel in der Auswahl (i = 0..2) oder „Beenden“ im Programm (i = None)."""
        if i is None:
            self.sm.leave()
        else:
            self.sm.select(i)

    # ---- Ereignisschleife (Hauptthread) -------------------------------------
    def _poll(self):
        try:
            while True:
                ev = self.q.get_nowait()
                kind = ev[0]
                if DEBUG and kind not in ("view", "tilt_angle"):
                    log("Ereignis", ev, "Zustand", self.sm.state)
                if kind == "view":
                    self.view.update(**ev[1])
                elif kind == "ble":
                    self.sm.on_ble(ev[1])
                elif kind == "button":
                    self.sm.on_button(ev[1], bool(ev[2]))
                elif kind == "knob":
                    self.sm.on_knob(ev[1])
                elif kind == "knob_press":
                    self.sm.on_knob_press()
                elif kind == "presence":
                    self.sm.on_presence(bool(ev[1]))
                elif kind == "distance":
                    self.sm.on_distance(int(ev[1]))
                elif kind == "tilt":
                    self.sm.on_tilt(int(ev[1]))
                elif kind == "tilt_angle":
                    self.view.update(tilt_deg=int(ev[1]) * TILT_SIGN)
        except queue.Empty:
            pass
        self.sm.tick()
        now = time.monotonic()
        if now >= self._next_slow:
            self._next_slow = now + 2.0
            self.set_state(self.sm.state, periodic=True)  # MCU nach Reset wieder synchron (nur notify)
            if now >= self._next_display:
                self._next_display = now + 60.0
                keep_display_on()
            if self.ble:  # keine BLE-Suche, während das Sprachmodell rechnet
                self.ble.quiet = self.speech.target is not None
            self.view.update(wlan=wlan_up(), ram_free_mb=ram_free_mb(),
                             nicla=bool(self.ble and self.ble.alive()))
        self.view.render()
        self.root.after(50, self._poll)


def main():
    global DEBUG
    ap = argparse.ArgumentParser(description="Lingo – Stationsroboter (UNO Q)")
    ap.add_argument("--window", action="store_true", help="Fenster statt Vollbild")
    ap.add_argument("--debug", action="store_true", help="Logs + CSV-Zeile pro Interaktion")
    ap.add_argument("--no-ble", action="store_true")
    ap.add_argument("--save", action="store_true", help="Audio speichern (nur Debug)")
    a = ap.parse_args()
    DEBUG = a.debug or os.environ.get("DEBUG") == "1"
    s2s.DEBUG = DEBUG
    if DEBUG:
        os.environ["DEBUG"] = "1"
        faulthandler.register(signal.SIGUSR1)  # kill -USR1 <pid>: Stack aller Threads ins Log
    App(a).start()


if __name__ == "__main__":
    main()
