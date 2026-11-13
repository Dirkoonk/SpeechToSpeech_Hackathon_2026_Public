"""Lingo Speech-to-Speech-Pipeline für den UNO Q (nur lokal).

Mikro (arecord) -> Vosk (STT) -> llama-server (Qwen3.5-0.8B) -> Piper (TTS) -> aplay

RAM-Sparen:
- Modelle werden erst bei Bedarf geladen (EN-Modelle nur im Dolmetscher).
- Audio wird als Rohdaten über Pipes gestreamt, kein numpy/PortAudio im Prozess.
- Audio bleibt nur im RAM und wird nach der Antwort verworfen (Ausnahme: --save).
- Die LLM-Antwort wird gestreamt und Satz für Satz gesprochen (kürzere Wartezeit).

Aufrufe (auf dem Board, im venv ~/lingo-venv):
  python s2s_pipeline.py --selftest [--play]       # ohne Mikro: TTS->STT->LLM->TTS
  python s2s_pipeline.py --mode chat               # Frage stellen -> gesprochene Antwort
  python s2s_pipeline.py --mode translate --src de # DE -> EN (--src en: EN -> DE)
  python s2s_pipeline.py --mode echo               # nur STT -> TTS
  Optionen: --debug (Zeiten/Logs), --save (Audio nach /tmp, nur Debug), --stt vosk
Umgebungsvariablen: LINGO_MIC / LINGO_SPK (ALSA-Gerät, z. B. plughw:1,0), LINGO_MODELS, LLAMA_URL
"""
import argparse
import itertools
import json
import os
import queue
import re
import select
import subprocess
import sys
import threading
import time
import urllib.request
import wave

MODELS = os.environ.get("LINGO_MODELS", "/home/arduino/models")
VOSK_PATHS = {
    "de": f"{MODELS}/vosk/vosk-model-small-de-0.15",
    "en": f"{MODELS}/vosk/vosk-model-small-en-us-0.15",
}
PIPER_PATHS = {
    "de": f"{MODELS}/piper/de_DE-thorsten-low.onnx",
    "en": f"{MODELS}/piper/en_US-lessac-low.onnx",
}
LLAMA_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8080")
MIC = os.environ.get("LINGO_MIC", "default")
SPK = os.environ.get("LINGO_SPK", "default")
RATE = 16000  # Vosk-Eingang
MIN_CHARS = 4  # kürzere Erkennungen ("ist", "ja") sind meist Geräusche

DEBUG = os.environ.get("DEBUG") == "1"

NO_INFO = "Dazu habe ich keine Information, bitte fragen Sie das Personal."
# System-Prompts bleiben FEST und kurz: llama-server rechnet sie nur einmal
# (Aufwärmen) und nutzt sie danach aus dem Cache. Daten kommen in die Nutzer-Nachricht.
# Jedes Prompt-Token kostet auf dem UNO Q ~90 ms.
SYS_CHAT = ("Du bist Lingo, ein freundlicher Stationsroboter in einer Klinik. Antworte immer nur auf "
            "Deutsch, in ein bis zwei kurzen Sätzen und nur mit den DATEN. Erfinde nichts.")
# Ein gemeinsamer System-Prompt für beide Richtungen: bleibt im Cache, auch wenn die
# Sprecher (Mikro kippen) ständig wechseln. Die Richtung steht in der Nutzer-Nachricht
# (am Board getestet: eindeutige Formulierung verhindert falsche Zielsprachen).
SYS_TRANSLATE = ("You are an interpreter in a German hospital. Translate between German and English only. "
                 "Output only the translation.")
# Jedes Thema hat einen eigenen Platz (Slot) im llama-server. Dort bleibt der gelesene
# Systemtext samt DATEN dauerhaft im Speicher: Bei einer Frage liest das Modell nur noch die
# Frage selbst (~20 statt ~100–190 Token, ~90 ms pro Token). Kostet ~260 MB RAM mehr.
SLOT = {"dolmetscher": 0, "besuch": 1, "essen": 2, "weg": 3, "wetter": 4, "kontakt": 5, "wlan": 6,
        "ruhe": 7, "station": 8}
JOKE_PREFIX = "Erzähle genau diesen Witz: "


def sys_chat(data):
    """Systemtext des Assistenten mit den DATEN eines Themas (fester Text je Thema = cachebar)."""
    return SYS_CHAT + "\nDATEN: " + data


TRANSLATE_PREFIX = {
    ("de", "en"): "Translate this German text into English: ",
    ("en", "de"): "Übersetze diesen englischen Text ins Deutsche: ",
}


def log(*a):
    if DEBUG:
        print("[debug]", *a, file=sys.stderr, flush=True)


def _save_wav(pcm, rate, tag):
    path = f"/tmp/lingo_{tag}_{int(time.time())}.wav"
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    log("Audio gespeichert:", path)


def proc_cpu_s(pid):
    """CPU-Zeit (user+system) eines fremden Prozesses, z. B. llama-server."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().rsplit(")", 1)[1].split()
        return (int(parts[11]) + int(parts[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError):
        return None


def llama_pid():
    try:
        out = subprocess.run(["pgrep", "-f", "llama-server"], capture_output=True, text=True).stdout.split()
        return int(out[0]) if out else None
    except (OSError, ValueError):
        return None


def trim_memory():
    """Nach dem Entladen von Modellen freien Heap wirklich ans System zurückgeben (glibc)."""
    import gc
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def rss_mb(pid="self"):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# STT (Vosk)
# ---------------------------------------------------------------------------
class Stt:
    def __init__(self):
        self._models = {}

    def model(self, lang):
        if lang not in self._models:
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)
            t = time.monotonic()
            self._models[lang] = Model(VOSK_PATHS[lang])
            log(f"Vosk {lang} geladen in {time.monotonic() - t:.1f}s")
        return self._models[lang]

    def unload(self, lang):
        self._models.pop(lang, None)

    def _rec(self, lang):
        from vosk import KaldiRecognizer
        return KaldiRecognizer(self.model(lang), RATE)

    def transcribe(self, pcm, lang):
        """PCM (16 kHz, s16le, mono) -> Text. Für Selbsttest und Benchmarks."""
        rec = self._rec(lang)
        for i in range(0, len(pcm), 8000):
            rec.AcceptWaveform(pcm[i:i + 8000])
        return json.loads(rec.FinalResult()).get("text", "")

    def listen(self, lang, start_timeout=8.0, max_s=15.0, stop=None, save=False, on_partial=None):
        """Vom Mikro hören, bis Vosk ein Satzende erkennt. Mikro nur während des Zuhörens offen."""
        rec = self._rec(lang)
        cmd = ["arecord", "-q", "-D", MIC, "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        fd = p.stdout.fileno()
        pending = b""
        mic_error = False
        t_data = time.monotonic()
        t0 = time.monotonic()
        heard = False
        text = ""
        cpu = 0.0
        buf = bytearray() if save else None
        t_last_audio = t0
        try:
            while True:
                # Nie blockierend lesen: liefert das Mikro nichts (USB hängt), nach 2 s abbrechen;
                # `stop` (Taste, Programmwechsel) wirkt spätestens nach 0,2 s.
                if stop is not None and stop.is_set():
                    break
                if not select.select([fd], [], [], 0.2)[0]:
                    if time.monotonic() - t_data > 2.0:
                        log("Mikro liefert keine Daten – Aufnahme neu starten")
                        mic_error = True
                        time.sleep(0.5)
                        break
                    continue
                chunk = os.read(fd, 4000)
                if not chunk:
                    mic_error = True  # arecord beendet (Gerät belegt/abgezogen)
                    time.sleep(0.5)
                    break
                t_data = time.monotonic()
                pending += chunk
                if len(pending) < 4000:  # 125 ms sammeln
                    continue
                data, pending = pending, b""
                if buf is not None:
                    buf += data
                c0 = time.thread_time()
                final = rec.AcceptWaveform(data)
                if final:
                    text = json.loads(rec.Result()).get("text", "")
                    cpu += time.thread_time() - c0
                    t_last_audio = time.monotonic()
                    if text:
                        break
                else:
                    partial = json.loads(rec.PartialResult()).get("partial", "")
                    cpu += time.thread_time() - c0
                    if partial:
                        heard = True
                        t_last_audio = time.monotonic()
                        if on_partial:
                            on_partial(partial)
                el = time.monotonic() - t0
                if (not heard and el > start_timeout) or el > max_s or (stop is not None and stop.is_set()):
                    break
            if not text:
                c0 = time.thread_time()
                text = json.loads(rec.FinalResult()).get("text", "")
                cpu += time.thread_time() - c0
        finally:
            p.kill()
            p.wait()
        if save and buf:
            _save_wav(bytes(buf), RATE, "mic")
        if text and len(text.replace(" ", "")) < MIN_CHARS:
            log("ignoriert (zu kurz, wohl Geräusch):", text)
            text = ""
        stats = {"stt_wall_s": round(time.monotonic() - t_last_audio, 3), "stt_cpu_s": round(cpu, 3),
                 "listen_s": round(time.monotonic() - t0, 2), "mic_error": mic_error}
        return text, stats


# ---------------------------------------------------------------------------
# TTS (Piper) + Wiedergabe (aplay)
# ---------------------------------------------------------------------------
class Tts:
    CACHE_MAX = 64  # gemerkte fertige Sätze (Ansagen, wiederholte Antworten), ~0,1 MB pro Satz

    def __init__(self):
        self._voices = {}
        self._pcm = {}  # (Sprache, Text) -> (PCM, Rate)

    def voice(self, lang):
        if lang not in self._voices:
            from piper import PiperVoice
            t = time.monotonic()
            self._voices[lang] = PiperVoice.load(PIPER_PATHS[lang])
            log(f"Piper {lang} geladen in {time.monotonic() - t:.1f}s")
        return self._voices[lang]

    def unload(self, lang):
        self._voices.pop(lang, None)

    def loaded(self, lang):
        return lang in self._voices

    def warmup(self, lang):
        """Erste ONNX-Inferenz ist langsam -> einmal vorab ausführen."""
        t = time.monotonic()
        self.synth("Hallo.", lang)
        return time.monotonic() - t

    def synth(self, text, lang):
        """Text -> (PCM, Rate). Schon einmal gesprochene Sätze kommen sofort aus dem RAM."""
        key = (lang, text)
        hit = self._pcm.get(key)
        if hit:
            return hit
        v = self.voice(lang)
        chunks = list(v.synthesize(text))
        pcm = b"".join(c.audio_int16_bytes for c in chunks)
        if len(self._pcm) >= self.CACHE_MAX:
            self._pcm.pop(next(iter(self._pcm)))  # ältesten Eintrag verwerfen
        self._pcm[key] = (pcm, v.config.sample_rate)
        return pcm, v.config.sample_rate


_AUDIO_LOCK = threading.Lock()  # nur eine Wiedergabe gleichzeitig (Ansagen + Antworten)


def play(pcm, rate, cancel=None):
    """PCM abspielen; bricht sofort ab, wenn `cancel` gesetzt wird (z. B. Taste gedrückt)."""
    if not pcm:
        return
    with _AUDIO_LOCK:
        if cancel is not None and cancel.is_set():
            return
        _play(pcm, rate, cancel)


def _play(pcm, rate, cancel):
    p = subprocess.Popen(["aplay", "-q", "-D", SPK, "-f", "S16_LE", "-r", str(rate), "-c", "1", "-t", "raw"],
                         stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        step = rate // 5 * 2  # 200 ms
        for i in range(0, len(pcm), step):
            if cancel is not None and cancel.is_set():
                p.kill()
                break
            p.stdin.write(pcm[i:i + step])
        p.stdin.close()
    except BrokenPipeError:
        pass
    p.wait()


class Speaker:
    """Spricht Sätze nacheinander. Synthese (Thread 1) und Wiedergabe (Thread 2)
    überlappen: Während Satz 1 läuft, wird Satz 2 schon erzeugt."""

    def __init__(self, tts, audible=True, save=False, cancel=None):
        self.tts = tts
        self.cancel = cancel
        self.audible = audible
        self.save = save
        self._q_text = queue.Queue()
        self._q_pcm = queue.Queue(maxsize=2)
        self.first_audio_t = None
        self.tts_wall = 0.0
        self.tts_cpu = 0.0
        self._t_syn = threading.Thread(target=self._synth_loop, daemon=True)
        self._t_play = threading.Thread(target=self._play_loop, daemon=True)
        self._t_syn.start()
        self._t_play.start()

    def say(self, text, lang):
        text = text.strip()
        if text:
            self._q_text.put((text, lang))

    def _synth_loop(self):
        while True:
            item = self._q_text.get()
            if item is None:
                self._q_pcm.put(None)
                return
            if self.cancel is not None and self.cancel.is_set():
                continue
            t, c = time.monotonic(), time.process_time()
            pcm, rate = self.tts.synth(*item)
            self.tts_wall += time.monotonic() - t
            self.tts_cpu += time.process_time() - c
            self._q_pcm.put((pcm, rate))

    def _play_loop(self):
        while True:
            item = self._q_pcm.get()
            if item is None:
                return
            if self.cancel is not None and self.cancel.is_set():
                continue
            if self.first_audio_t is None:
                self.first_audio_t = time.monotonic()
            if self.save:
                _save_wav(item[0], item[1], "tts")
            if self.audible:
                play(*item, cancel=self.cancel)

    def finish(self):
        """Warten, bis alles gesprochen ist."""
        self._q_text.put(None)
        self._t_syn.join()
        self._t_play.join()


# ---------------------------------------------------------------------------
# LLM (llama-server, OpenAI-kompatible API)
# ---------------------------------------------------------------------------
_THINK = re.compile(r"</?think>")


class Llm:
    def __init__(self, url=LLAMA_URL):
        self.url = url
        self.last = {}

    def health(self):
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=2) as r:
                return b"ok" in r.read()
        except OSError:
            return False

    def _post(self, body, timeout=120):
        req = urllib.request.Request(self.url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=timeout)

    @staticmethod
    def _body(messages, max_tokens, stream, slot=None):
        body = {"messages": messages, "max_tokens": max_tokens, "temperature": 0.2, "top_p": 0.9,
                "stream": stream, "cache_prompt": True,
                "chat_template_kwargs": {"enable_thinking": False}}
        if slot is not None:
            body["id_slot"] = slot  # fester Platz im Server (siehe SLOT)
        return body

    def stream(self, messages, max_tokens=80, slot=None):
        """Liefert Text-Stücke, sobald sie erzeugt werden."""
        self.last = {}
        with self._post(self._body(messages, max_tokens, True, slot)) as r:
            for line in r:
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    break
                d = json.loads(payload)
                if "timings" in d:
                    self.last = d["timings"]
                for ch in d.get("choices") or []:
                    piece = (ch.get("delta") or {}).get("content")
                    if piece:
                        yield piece

    def complete(self, messages, max_tokens=80, slot=None):
        with self._post(self._body(messages, max_tokens, False, slot)) as r:
            d = json.load(r)
        self.last = d.get("timings", {})
        return _THINK.sub("", d["choices"][0]["message"]["content"]).strip()

    def warmup(self, system_prompt, slot=None):
        """Q1.5: Modell-Seiten in den RAM holen und System-Prompt im KV-Cache (des Slots) ablegen."""
        t = time.monotonic()
        try:
            self.complete([{"role": "system", "content": system_prompt},
                           {"role": "user", "content": "Hallo"}], max_tokens=1, slot=slot)
        except OSError as e:
            log("Aufwärmen fehlgeschlagen:", e)
        return time.monotonic() - t


def split_sentences(pieces, min_len=12):
    """Text-Stücke zu ganzen Sätzen zusammensetzen (für frühes Sprechen)."""
    buf = ""
    end = re.compile(r"(.*?[.!?…])\s+(.*)", re.S)
    for p in pieces:
        buf += p
        m = end.match(buf)
        while m and len(m.group(1)) >= min_len:
            yield _THINK.sub("", m.group(1)).strip()
            buf = m.group(2)
            m = end.match(buf)
    rest = _THINK.sub("", buf).strip()
    if rest:
        yield rest


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
class Pipeline:
    def __init__(self, audible=True, save=False):
        self.stt = Stt()
        self.tts = Tts()
        self.llm = Llm()
        self.audible = audible
        self.save = save
        self.cancel = threading.Event()  # gesetzt = laufende Antwort sofort abbrechen
        self._answers = {}  # (Frage normalisiert, Daten) bzw. (Richtung, Satz) -> fertige Antwort

    def _say_cached(self, answer, lang, on_text):
        """Schon bekannte Antwort ohne Sprachmodell sprechen."""
        t = time.monotonic()
        if on_text:
            on_text(answer)
        self.say(answer, lang)
        d = round(time.monotonic() - t, 3)
        return answer, {"llm_wall_s": 0, "tts_wall_s": d, "first_audio_s": None}

    def _remember(self, key, answer):
        if answer and not self.cancel.is_set():
            if len(self._answers) >= 200:
                self._answers.pop(next(iter(self._answers)))
            self._answers[key] = answer

    def _run_llm(self, messages, out_lang, on_text=None, max_tokens=80, slot=None):
        """LLM streamen und Satz für Satz sprechen. Gibt (Antwort, Statistik) zurück."""
        spk = Speaker(self.tts, self.audible, self.save, self.cancel)
        pid = llama_pid()  # llama-server wird je Programm neu gestartet
        cpu0 = proc_cpu_s(pid) if pid else None
        t0 = time.monotonic()
        parts = []
        # Abbruch pro Token prüfen (nicht erst am Satzende): Taste muss sofort wirken
        tokens = itertools.takewhile(lambda _: not self.cancel.is_set(),
                                     self.llm.stream(messages, max_tokens, slot))
        for sentence in split_sentences(tokens):
            if self.cancel.is_set():
                break
            parts.append(sentence)
            if on_text:
                on_text(" ".join(parts))
            spk.say(sentence, out_lang)
        t_llm = time.monotonic() - t0
        cpu1 = proc_cpu_s(pid) if pid else None
        spk.finish()
        tm = self.llm.last
        stats = {
            "llm_prompt_tokens": tm.get("prompt_n"), "llm_gen_tokens": tm.get("predicted_n"),
            "llm_wall_s": round(t_llm, 3),
            "llm_cpu_s": round(cpu1 - cpu0, 2) if cpu0 is not None and cpu1 is not None else None,
            "tts_wall_s": round(spk.tts_wall, 3), "tts_cpu_s": round(spk.tts_cpu, 3),
            "first_audio_s": round(spk.first_audio_t - t0, 3) if spk.first_audio_t else None,
        }
        return " ".join(parts), stats

    def say(self, text, lang="de", cancel=None):
        spk = Speaker(self.tts, self.audible, self.save, cancel or self.cancel)
        spk.say(text, lang)
        spk.finish()

    def chat(self, question, data="", on_text=None, topic=None):
        """Assistent: Antwort nur aus DATEN. Ohne Daten feste Antwort ohne LLM.
        `topic` (aus knowledge.topic) wählt den Slot, in dem die DATEN schon gelesen sind."""
        if not data:
            return self._say_cached(NO_INFO, "de", on_text)
        if topic == "witz" or data.startswith(JOKE_PREFIX):
            # Der Witz steht fertig in den Daten: direkt sprechen statt vom Modell abschreiben lassen
            joke = data[len(JOKE_PREFIX):] if data.startswith(JOKE_PREFIX) else data
            return self._say_cached(joke, "de", on_text)
        key = (" ".join(question.lower().split()), data)
        if key in self._answers:  # gleiche Frage, gleiche Daten: Antwort ist schon bekannt
            return self._say_cached(self._answers[key], "de", on_text)
        messages = [{"role": "system", "content": sys_chat(data)},
                    {"role": "user", "content": question}]
        answer, st = self._run_llm(messages, "de", on_text, slot=SLOT.get(topic))
        self._remember(key, answer)
        return answer, st

    def translate(self, text, src, dst, on_text=None):
        """Dolmetscher: erst Phrasenbibliothek (geprüft, schnell), sonst LLM."""
        try:
            import knowledge
            hit = knowledge.phrase(text, src)
        except ImportError:
            hit = None
        if hit:
            if on_text:
                on_text(hit)
            t = time.monotonic()
            self.say(hit, dst)
            return hit, {"llm_wall_s": 0, "tts_wall_s": round(time.monotonic() - t, 3), "uebersetzer": "phrasen"}
        key = (src + ">" + dst, " ".join(text.lower().split()))
        if key in self._answers:  # derselbe Satz wurde schon übersetzt
            answer, st = self._say_cached(self._answers[key], dst, on_text)
            st["uebersetzer"] = "cache"
            return answer, st
        messages = [{"role": "system", "content": SYS_TRANSLATE},
                    {"role": "user", "content": TRANSLATE_PREFIX[(src, dst)] + text}]
        answer, st = self._run_llm(messages, dst, on_text, max_tokens=100, slot=SLOT["dolmetscher"])
        self._remember(key, answer)
        return answer, st


# ---------------------------------------------------------------------------
# Kommandozeile
# ---------------------------------------------------------------------------
def _knowledge_for(question):
    try:
        import knowledge
        return knowledge.lookup(question)
    except ImportError:
        return ""


def selftest(pl, audible):
    print("== Selbsttest (ohne Mikrofon) ==")
    ok = pl.llm.health()
    print("llama-server:", "OK" if ok else "NICHT ERREICHBAR (bash ~/lingo/unoq/start_llama.sh)")
    if not ok:
        return 1
    print(f"Aufwärmen LLM (Chat): {pl.llm.warmup(SYS_CHAT):.2f}s")
    print(f"Aufwärmen Piper DE: {pl.tts.warmup('de'):.2f}s")

    q = "Wann sind die Besuchszeiten?"
    t = time.monotonic()
    pcm, rate = pl.tts.synth(q, "de")
    print(f"TTS DE ({rate} Hz): {time.monotonic() - t:.2f}s für {len(pcm) / 2 / rate:.1f}s Audio")
    if rate != RATE:
        print("Hinweis: Stimme hat nicht 16 kHz, STT-Test übersprungen")
        heard = q
    else:
        t = time.monotonic()
        heard = pl.stt.transcribe(pcm, "de")
        print(f"STT DE: '{heard}' in {time.monotonic() - t:.2f}s")
    del pcm

    data = _knowledge_for(heard) or "Besuchszeiten: Täglich von 14 bis 19 Uhr."
    t = time.monotonic()
    answer, st = pl.chat(heard, data)
    print(f"Chat: '{answer}'")
    print(f"  {st} gesamt {time.monotonic() - t:.2f}s")

    q2 = "Wann gibt es Mittagessen?"
    t = time.monotonic()
    answer, st = pl.chat(q2, _knowledge_for(q2))
    print(f"Chat 2 (Cache warm): '{answer}'")
    print(f"  {st} gesamt {time.monotonic() - t:.2f}s")

    pl.llm.warmup(SYS_TRANSLATE)
    pl.tts.warmup("en")
    t = time.monotonic()
    answer, st = pl.translate("Ihre Tochter hat angerufen und kommt heute Abend vorbei.", "de", "en")
    print(f"Übersetzung DE->EN: '{answer}'")
    print(f"  {st} gesamt {time.monotonic() - t:.2f}s")

    print(f"RAM: Python {rss_mb()} MB, llama-server {rss_mb(pl._llama_pid) if pl._llama_pid else '?'} MB")
    return 0


def main():
    global DEBUG
    ap = argparse.ArgumentParser(description="Lingo Speech-to-Speech (lokal)")
    ap.add_argument("--mode", choices=["echo", "chat", "translate"], default="chat")
    ap.add_argument("--src", choices=["de", "en"], default="de", help="Sprache des Sprechers (translate)")
    ap.add_argument("--stt", choices=["vosk", "whisper"], default="vosk")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--play", action="store_true", help="Selbsttest hörbar abspielen")
    ap.add_argument("--once", action="store_true", help="nur eine Runde")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--save", action="store_true", help="Audio nach /tmp speichern (nur Debug)")
    a = ap.parse_args()
    DEBUG = DEBUG or a.debug
    if a.stt == "whisper":
        print("faster-whisper ist auf dem UNO Q aus RAM-Gründen nicht installiert, nutze Vosk.", file=sys.stderr)

    if a.selftest:
        sys.exit(selftest(Pipeline(audible=a.play, save=a.save), a.play))

    pl = Pipeline(save=a.save)
    if a.mode != "echo":
        pl.llm.warmup(SYS_CHAT if a.mode == "chat" else SYS_TRANSLATE)
    lang = a.src if a.mode == "translate" else "de"
    pl.stt.model(lang)
    print("Bereit. Bitte sprechen ... (Strg+C beendet)", flush=True)
    try:
        while True:
            text, st = pl.stt.listen(lang, save=a.save)
            if not text:
                if a.once:
                    break
                continue
            print("Gehört:", text, flush=True)
            t = time.monotonic()
            if a.mode == "echo":
                pl.say(text, lang)
                answer, st2 = text, {}
            elif a.mode == "chat":
                answer, st2 = pl.chat(text, _knowledge_for(text))
            else:
                dst = "en" if lang == "de" else "de"
                answer, st2 = pl.translate(text, lang, dst)
            print("Antwort:", answer, flush=True)
            log({**st, **st2, "gesamt_wall_s": round(time.monotonic() - t + st.get("stt_wall_s", 0), 3)})
            if a.once:
                break
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
