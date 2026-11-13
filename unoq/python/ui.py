"""Oberfläche (Q2.1–Q2.3, I.2, I.3): Tkinter-Vollbild, passt sich jedem Display an.

Gezeichnet wird in einem festen Raster von 600 Einheiten Höhe (Breite je nach
Seitenverhältnis, mindestens 1024); `_Scaled` rechnet Koordinaten, Schriftgrößen
und Linienstärken auf die echte Auflösung um (7"-Display 1024x600 genauso wie 1920x1080).

Stil: ruhig und aufgeräumt – einfarbiger Hintergrund, schlichte Felder, ein Blau als Akzent.
Die Augen schauen immer dorthin, wo gerade etwas passiert (gewählte Kachel, Fortschritt, Sprecher).

Ein einziger Canvas, gezeichnet nur bei Änderungen (RAM- und CPU-sparsam, keine Bilddateien).
Aufbau: Statuszeile · Inhalt des Zustands/Programms · Meldung · Tastenleiste.
Alle Aufrufe nur aus dem Tk-Hauptthread (update() + render()).
"""
import os
import time
import tkinter as tk

from state_machine import (ANFAHRT, ASSISTENT, AUSWAHL, DOLMETSCHER, NAMES, STANDBY, TILES, TILT_ON_DEG,
                           UEBERSICHT)

BG = "#10182b"        # Hintergrund
BAR = "#0b1120"       # Status- und Tastenleiste
PANEL = "#1e293b"     # Felder
LINE = "#334155"      # Rahmen, Trennlinien
FG = "#f1f5f9"
DIM = "#64748b"
ACCENT = "#38bdf8"    # Blau: gewählt / aktiv
SOFT = "#1e3a5f"      # gedämpftes Blau (Deutsch im Dolmetscher, Beenden-Feld)
VIOLET = "#a78bfa"    # Englische Seite im Dolmetscher
VIOLET_BG = "#3b2f5c"
OK = "#22c55e"
BAD = "#ef4444"
WARN = "#f59e0b"
FONT = "DejaVu Sans"

VH = 600          # Höhe des Rasters
INFO_S = 8.0      # so lange bleibt eine Meldung (Taste B/C …) stehen

TILE_LABELS = {ASSISTENT: "Assistent", DOLMETSCHER: "Dolmetscher", UEBERSICHT: "Übersicht"}
TILE_SUB = {ASSISTENT: "Fragen zur Station\n(Deutsch)", DOLMETSCHER: "Gespräch übersetzen\nDeutsch ⇄ English",
            UEBERSICHT: "Status und\nVerbindungen"}
DIRECTION_LABEL = {"de": "DE → EN", "en": "EN → DE"}
KEYS = (("A", "Ladestation"), ("B", "Material holen"), ("C", "Pflege rufen"))


class _Scaled:
    """Canvas-Hülle: Raster-Einheiten -> Pixel (Koordinaten, Schrift, Linienstärke, Umbruchbreite)."""

    def __init__(self, canvas, k):
        self.c, self.k = canvas, k

    def _kw(self, kw):
        k = self.k
        if "font" in kw:
            f = kw["font"]
            kw["font"] = (f[0], max(6, round(f[1] * k))) + tuple(f[2:])
        if kw.get("width"):
            kw["width"] = max(1, round(kw["width"] * k))
        return kw

    def _make(self, fn, xy, kw):
        return fn(*[v * self.k for v in xy], **self._kw(kw))

    def create_rectangle(self, *xy, **kw):
        return self._make(self.c.create_rectangle, xy, kw)

    def create_oval(self, *xy, **kw):
        return self._make(self.c.create_oval, xy, kw)

    def create_line(self, *xy, **kw):
        return self._make(self.c.create_line, xy, kw)

    def create_arc(self, *xy, **kw):
        return self._make(self.c.create_arc, xy, kw)

    def create_polygon(self, *xy, **kw):
        return self._make(self.c.create_polygon, xy, kw)

    def create_text(self, *xy, **kw):
        return self._make(self.c.create_text, xy, kw)

    def bbox(self, item):
        return tuple(v / self.k for v in self.c.bbox(item))

    def move(self, item, dx, dy):
        self.c.move(item, dx * self.k, dy * self.k)

    def delete(self, *items):
        self.c.delete(*items)

    def tag_lower(self, item):
        self.c.tag_lower(item)


class LingoUI:
    def __init__(self, root, on_tile=None, fullscreen=True):
        self.root = root
        self.on_tile = on_tile  # on_tile(i) = Kachel i starten, on_tile(None) = Programm beenden
        root.title("Lingo")
        root.configure(bg=BG)
        if fullscreen:
            root.attributes("-fullscreen", True)
            if os.environ.get("LINGO_HIDE_CURSOR") == "1":  # Zeiger nur auf Wunsch ausblenden
                root.config(cursor="none")
            pw, ph = root.winfo_screenwidth(), root.winfo_screenheight()
        else:
            pw, ph = (int(v) for v in os.environ.get("LINGO_WINDOW", "1024x600").split("x"))
            root.geometry(f"{pw}x{ph}")
        self.k = min(pw / 1024, ph / VH)
        self.W, self.H = pw / self.k, ph / self.k   # Rastergröße (H >= 600, W >= 1024)
        self.canvas = tk.Canvas(root, width=pw, height=ph, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._click)
        self.c = _Scaled(self.canvas, self.k)
        self.s = {
            "state": STANDBY, "tile": 0, "direction": "de",
            "distance_m": None, "distance_real": False,
            "nicla": False, "mcu": False, "wlan": False,
            "info": "", "handover": None, "loading": "",
            "listening": False, "speaking": False, "thinking": False, "partial": "",
            "sub_user": "", "sub_bot": "", "last_answer_s": None, "ram_free_mb": None,
            "chat": (),  # Dolmetscher: ((src, original, übersetzung), ...)
            "tilt_deg": None,  # Kippwinkel des Mikros (−links / +rechts), nur Dolmetscher
            "preload": "",     # Fortschritt beim Vorladen der Themen (Assistent)
            "models": "",      # welche Programme ihre Modelle im RAM haben
        }
        self._dirty = True
        self._blink_until = 0.0
        self._next_blink = time.monotonic() + 3
        self._info_until = 0.0
        self._phase = 0
        self._quit_box = None
        self._gaze = [0.0, 0.0]   # Blickrichtung der Augen, je −1 … +1 (x: links/rechts, y: oben/unten)

    # ---- API ----------------------------------------------------------------
    def update(self, **kw):
        if "state" in kw and kw["state"] != self.s["state"]:
            self.s.update(sub_user="", sub_bot="", partial="")  # alte Untertitel weg
        if kw.get("info"):
            self._info_until = time.monotonic() + INFO_S
        for k, v in kw.items():
            if self.s.get(k) != v:
                self.s[k] = v
                self._dirty = True

    def _gaze_target(self):
        """Wohin die Augen schauen sollen: immer zur gerade gewählten bzw. aktiven Stelle."""
        s = self.s
        st = s["state"]
        if st == AUSWAHL:                       # gewählte Kachel (links / Mitte / rechts, unten)
            return (s["tile"] - 1.0, 0.9)
        if st == ANFAHRT:                       # dem Fortschrittsbalken folgen
            d = s["distance_m"] or 0
            frac = max(0.0, min(1.0, 1 - (d - 0.5) / 4.5))
            return (-0.9 + 1.8 * frac, 0.9)
        if st == DOLMETSCHER:                   # zum Sprecher: Deutsch links, English rechts
            return (-1.0 if s["direction"] == "de" else 1.0, 0.0)
        if st == ASSISTENT:
            if s["info"]:
                return (0.0, 1.0)               # Meldung unten
            if s["thinking"] or s["loading"]:
                return (0.6, -0.8)              # „überlegt“: nach oben
            if s["speaking"] or s["sub_bot"]:
                return (0.0, 0.7)               # auf die Antwort darunter
            return (0.0, 0.0)                   # hört zu: schaut die Person an
        return (0.0, 0.0)

    def render(self):
        now = time.monotonic()
        s = self.s
        if s["info"] and now > self._info_until and s["state"] != ANFAHRT:
            s["info"] = ""      # Meldungen verschwinden von selbst
            self._dirty = True
        if now >= self._next_blink:  # gelegentliches Blinzeln, wenn wach
            self._blink_until = now + 0.15
            self._next_blink = now + 4.0
            self._dirty = True
        if self._blink_until and now > self._blink_until:
            self._blink_until = 0.0
            self._dirty = True
        if s["speaking"] or s["thinking"] or s["loading"]:
            # Animation: nur neu zeichnen, wenn sich das Bild wirklich ändert (max. 6×/s) –
            # volles Neuzeichnen mit 20 Hz nimmt dem Sprachmodell spürbar CPU weg
            phase = int(now * (6 if s["speaking"] else 3))
            if phase != self._phase:
                self._phase = phase
                self._dirty = True
        # Blick weich zum Ziel führen (wenige Bilder, dann Ruhe)
        tx, ty = self._gaze_target()
        gx, gy = self._gaze
        if abs(tx - gx) > 0.03 or abs(ty - gy) > 0.03:
            self._gaze = [gx + (tx - gx) * 0.45, gy + (ty - gy) * 0.45]
            self._dirty = True
        elif (gx, gy) != (tx, ty):
            self._gaze = [tx, ty]
        if not self._dirty:
            return
        self._dirty = False
        self.canvas.delete("all")
        st = s["state"]
        self._background()
        self._status_bar()
        if st == UEBERSICHT:
            self._overview()
        elif st == DOLMETSCHER:
            self._chat()
            self._speaker_bar()
        elif st == AUSWAHL:
            self._face(now, cy=128, scale=0.72)
            self._menu()
        elif st == ASSISTENT:
            self._face(now, cy=160, scale=0.9)
            self._subtitles(now)
        elif st == ANFAHRT:
            self._face(now, cy=180, scale=1.0)
            self._approach()
        else:
            self._face(now, cy=210, scale=1.1)
            if not s["info"]:
                self.c.create_text(self.W / 2, 420, text="„Hey Lingo“ sagen  ·  oder Taste A lang drücken",
                                   fill=DIM, font=(FONT, 18))
        if st != ANFAHRT:
            self._toast()
        self._key_bar()

    # ---- Bausteine ------------------------------------------------------------
    def _cham(self, x0, y0, x1, y1, cut=0, **kw):
        """Schlichtes Feld (Rechteck)."""
        return self.c.create_rectangle(x0, y0, x1, y1, **kw)

    def _panel(self, x0, y0, x1, y1, cut=0, fill=PANEL, edge=LINE, glow=False):
        self.c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=edge, width=1)

    def _background(self):
        """Einfarbiger Hintergrund (Canvas-Farbe) – nichts zu zeichnen."""

    def _eyes(self, cx, cy, f, dx, closed=False, ring=FG, lively=True):
        """Zwei Augen. Die Pupille wandert nach self._gaze (zur gewählten / aktiven Stelle)."""
        c = self.c
        gx, gy = self._gaze
        for ex in (cx - dx, cx + dx):
            if closed:
                c.create_arc(ex - 60 * f, cy - 30 * f, ex + 60 * f, cy + 40 * f, start=200, extent=140,
                             style="arc", outline=FG, width=8 * f)
                continue
            rx, ry = 58 * f, 72 * f
            c.create_oval(ex - rx, cy - ry, ex + rx, cy + ry, fill=FG, outline=ring, width=max(1, 10 * f))
            px, py = ex + gx * 26 * f, cy + 4 * f + gy * 34 * f
            r = 23 * f
            c.create_oval(px - r, py - r, px + r, py + r, fill=BG, width=0)
            if lively:
                c.create_oval(px + r * 0.25, py - r * 0.6, px + r * 0.6, py - r * 0.25, fill=FG, width=0)

    # ---- Bereiche -----------------------------------------------------------
    def _status_bar(self):
        c, s, W = self.c, self.s, self.W
        c.create_rectangle(0, 0, W, 44, fill=BAR, width=0)
        st = s["state"]
        c.create_oval(14, 15, 28, 29, fill=ACCENT if st != STANDBY else DIM, width=0)
        t = c.create_text(38, 22, text=NAMES[st], fill=FG, font=(FONT, 16, "bold"), anchor="w")
        x = c.bbox(t)[2]
        if st == DOLMETSCHER:
            col = ACCENT if s["direction"] == "de" else VIOLET
            t = c.create_text(x + 14, 22, text=DIRECTION_LABEL[s["direction"]], fill=col, font=(FONT, 14, "bold"),
                              anchor="w")
            x = c.bbox(t)[2]
        if st in (DOLMETSCHER, UEBERSICHT):
            # kleines Gesicht in der Statuszeile: schaut zum aktiven Sprecher
            self._eyes(x + 44, 22, 0.2, 17, lively=False)
        if s["distance_m"] is not None:
            mark = "" if s["distance_real"] else " (simuliert)"
            num = f"{s['distance_m']:.2f}".replace(".", ",")
            c.create_text(W / 2, 22, text=f"Abstand {num} m{mark}", fill=FG, font=(FONT, 14))
        if s["listening"]:
            c.create_oval(W / 2 + 150, 15, W / 2 + 164, 29, fill=ACCENT, width=0)
            c.create_text(W / 2 + 170, 22, text="Mikro an", fill=ACCENT, font=(FONT, 13), anchor="w")
        # Verbindungen: grün = verbunden. MCU ist Pflicht (rot bei Ausfall); Nicla (Wake-Word-Gerät)
        # und WLAN (nur Wetter) sind optional -> grau „aus“ statt Fehler-Rot.
        x = W - 14
        for label, on, needed in (("WLAN", s["wlan"], False), ("Nicla", s["nicla"], False),
                                  ("MCU", s["mcu"], True)):
            text = label if on or needed else label + " aus"
            col = OK if on else (BAD if needed else DIM)
            c.create_oval(x - 12, 16, x, 28, fill=col, width=0)
            t = c.create_text(x - 18, 22, text=text, fill=FG if on or needed else DIM, font=(FONT, 13), anchor="e")
            x = c.bbox(t)[0] - 16

    def _face(self, now, cy=190, scale=1.0):
        c, s, W = self.c, self.s, self.W
        f = scale
        asleep = s["state"] == STANDBY
        closed = asleep or now < self._blink_until
        self._eyes(W / 2, cy, f, 150 * f, closed=closed, ring=ACCENT if s["listening"] else FG)
        if asleep:
            c.create_text(W / 2 + 230 * f, cy - 80 * f, text="z z z", fill=DIM, font=(FONT, 28, "italic"))
        else:
            my = cy + 105 * f
            if s["speaking"]:
                h = (10 + 16 * (self._phase % 2)) * f
                c.create_oval(W / 2 - 46 * f, my - h / 2, W / 2 + 46 * f, my + h / 2, fill=FG, width=0)
            elif s["thinking"] or s["loading"]:
                for i in range(3):
                    on = int(now * 3) % 3 == i
                    c.create_oval(W / 2 - 42 * f + i * 35 * f, my - 7 * f, W / 2 - 28 * f + i * 35 * f, my + 7 * f,
                                  fill=FG if on else DIM, width=0)
            else:
                c.create_line(W / 2 - 40 * f, my, W / 2 + 40 * f, my, fill=FG, width=6 * f, capstyle="round")
        self._handover_icons()

    def _handover_icons(self):
        """I.3: Nicla-Symbol (gerufen) -> Mikrofon-Symbol (Person da)."""
        h = self.s["handover"]
        if not h:
            return
        c, x, y = self.c, self.W - 110, 66
        nicla_on = h in ("nicla", "taste")
        col = ACCENT if nicla_on else DIM
        c.create_rectangle(x - 100, y, x - 60, y + 54, outline=col, width=3)
        c.create_text(x - 80, y + 27, text="N" if h != "taste" else "A", fill=col, font=(FONT, 18, "bold"))
        c.create_text(x - 80, y + 72, text="gerufen", fill=col, font=(FONT, 11))
        c.create_text(x - 24, y + 27, text="›", fill=DIM, font=(FONT, 28))
        mic = ACCENT if h == "mikro" else DIM
        c.create_rectangle(x + 18, y + 2, x + 42, y + 36, outline=mic, width=3)
        c.create_arc(x + 10, y + 14, x + 50, y + 46, start=180, extent=180, style="arc", outline=mic, width=3)
        c.create_line(x + 30, y + 46, x + 30, y + 56, fill=mic, width=3)
        c.create_text(x + 30, y + 72, text="angekommen", fill=mic, font=(FONT, 11))

    def _approach(self):
        """I.2: „Bin unterwegs …“ + Abstandsbalken."""
        c, s, W = self.c, self.s, self.W
        c.create_text(W / 2, 370, text=s["info"] or "Bin unterwegs", fill=FG, font=(FONT, 26, "bold"),
                      width=W - 120, justify="center")
        d = s["distance_m"] or 0
        frac = max(0.0, min(1.0, 1 - (d - 0.5) / 4.5))
        x0, x1, y = 160, W - 160, 430
        c.create_rectangle(x0, y, x1, y + 22, fill=PANEL, width=0)
        c.create_rectangle(x0, y, x0 + (x1 - x0) * frac, y + 22, fill=ACCENT, width=0)
        c.create_text(W / 2, y + 50, text=f"noch {d:.1f} m".replace(".", ","), fill=DIM, font=(FONT, 16))

    # ---- Auswahl --------------------------------------------------------------
    def _tile_boxes(self):
        n, gap, y0, y1 = len(TILES), 28, 292, 452
        w = (self.W - gap * (n + 1)) / n
        return [(gap + i * (w + gap), y0, gap + i * (w + gap) + w, y1) for i in range(n)]

    def _menu(self):
        """Q2.2: Drei Programme zur Wahl. Nichts läuft, bis eines gestartet wird."""
        c, s, W = self.c, self.s, self.W
        c.create_text(W / 2, 256, text="Was möchten Sie tun?", fill=FG, font=(FONT, 22, "bold"))
        for i, (x0, y0, x1, y1) in enumerate(self._tile_boxes()):
            sel = i == s["tile"]
            c.create_rectangle(x0, y0, x1, y1, fill=ACCENT if sel else PANEL, width=0)
            mx = (x0 + x1) / 2
            c.create_text(mx, y0 + 50, text=TILE_LABELS[TILES[i]], fill=BG if sel else FG,
                          font=(FONT, 24, "bold"))
            c.create_text(mx, y0 + 108, text=TILE_SUB[TILES[i]], fill=BG if sel else DIM,
                          font=(FONT, 14), justify="center")

    # ---- Assistent ------------------------------------------------------------
    def _subtitles(self, now):
        c, s, W = self.c, self.s, self.W
        y = 300
        user = s["partial"] or s["sub_user"]
        if s["loading"]:
            dots = "." * (1 + int(now * 3) % 3)
            c.create_text(W / 2, y + 60, text=f"Assistent {s['loading'].rstrip(' …')} {dots}", fill=DIM,
                          font=(FONT, 20))
            return
        if user:
            c.create_text(W / 2, y, text="„" + user + "“", fill=DIM, font=(FONT, 17), anchor="n",
                          width=W - 80, justify="center")
        if s["sub_bot"]:
            c.create_text(W / 2, y + 62, text=s["sub_bot"], fill=FG, font=(FONT, 22, "bold"), anchor="n",
                          width=W - 80, justify="center")
        elif s["listening"] and not user:
            c.create_text(W / 2, y + 62, text="Fragen Sie mich etwas …", fill=DIM, font=(FONT, 20), anchor="n")
        if s["preload"] and not s["info"]:
            c.create_text(W / 2, self.H - 62, text=s["preload"], fill=DIM, font=(FONT, 12))

    # ---- Übersicht ------------------------------------------------------------
    def _overview(self):
        """Q2.3: Übersichtsseite."""
        c, s, W = self.c, self.s, self.W
        x, y = 90, 92
        rows = [
            ("Nicla (Wake-Word)", "verbunden" if s["nicla"] else "nicht verbunden – Ruf über Taste A lang",
             OK if s["nicla"] else DIM),
            ("WLAN", "verbunden" if s["wlan"] else "getrennt (nur für Wetter nötig)", OK if s["wlan"] else DIM),
            ("MCU / Modulinos", "verbunden" if s["mcu"] else "getrennt", OK if s["mcu"] else BAD),
            ("Letzte Antwortzeit", f"{s['last_answer_s']:.1f} s".replace(".", ",") if s["last_answer_s"] else "–",
             None),
            ("RAM frei", f"{s['ram_free_mb']} MB" if s["ram_free_mb"] else "–", None),
            ("Sprachmodelle", s["models"] or "entladen – starten erst mit einem Programm", None),
        ]
        self._panel(x - 30, y - 34, W - 60, y + 50 * len(rows) + 62, 18)
        for k, v, dot in rows:
            c.create_text(x, y, text=k, fill=DIM, font=(FONT, 16), anchor="w")
            vx = x + 290
            if dot:
                c.create_rectangle(vx, y - 6, vx + 12, y + 6, fill=dot, width=0)
                vx += 24
            c.create_text(vx, y, text=v, fill=FG, font=(FONT, 16, "bold"), anchor="w")
            c.create_line(x, y + 25, W - 90, y + 25, fill=LINE, width=1)
            y += 50
        y += 10
        c.create_text(x, y + 6, text="Abstand", fill=DIM, font=(FONT, 16), anchor="w")
        d = s["distance_m"]
        x0, x1 = x + 290, W - 190
        c.create_rectangle(x0, y - 6, x1, y + 18, fill=BG, outline=LINE, width=1)
        if d is not None:
            frac = max(0.0, min(1.0, d / 1.5))
            col = OK if d < 0.5 else (WARN if d < 0.65 else ACCENT)
            c.create_rectangle(x0, y - 6, x0 + (x1 - x0) * frac, y + 18, fill=col, width=0)
            c.create_text(W - 90, y + 6, text=f"{d:.2f} m".replace(".", ","), fill=FG, font=(FONT, 15, "bold"),
                          anchor="e")

    # ---- Dolmetscher: Chat ----------------------------------------------------
    BUBBLE = {"de": SOFT, "en": VIOLET_BG}
    EDGE = {"de": ACCENT, "en": VIOLET}
    LANG_NAME = {"de": "Deutsch", "en": "English"}

    def _chat(self):
        """Gesprächsverlauf: Deutsch links, Englisch rechts; neueste Nachricht unten.
        Je Blase: Original (klein) + Übersetzung (groß)."""
        c, s, W = self.c, self.s, self.W
        top, bottom, gap, pad = 54, 366, 10, 11
        maxw = int(W * 0.66)
        entries = list(s["chat"])
        if s["listening"] and s["partial"]:
            entries.append((s["direction"], s["partial"], None))  # gerade gesprochen
        if not entries:
            if s["loading"]:
                text = f"Dolmetscher {s['loading']}"
            else:
                text = "Dolmetscher bereit.\nMikro nach links kippen: Deutsch  ·  nach rechts: English"
            c.create_text(W / 2, (top + bottom) / 2, text=text, fill=DIM, font=(FONT, 19), justify="center")
            return
        y = bottom
        for src, orig, trans in reversed(entries):
            left = src == "de"
            t1 = c.create_text(0, 0, text=orig, fill="#cbd5e1", font=(FONT, 13), anchor="nw", width=maxw)
            if trans is None:
                t2_text, t2_font = "…", (FONT, 15, "italic")
            else:
                t2_text, t2_font = trans or "…", (FONT, 18, "bold")
            t2 = c.create_text(0, 0, text=t2_text, fill=FG, font=t2_font, anchor="nw", width=maxw)
            b1, b2 = c.bbox(t1), c.bbox(t2)
            w = max(b1[2] - b1[0], b2[2] - b2[0]) + 2 * pad + 6
            h1 = b1[3] - b1[1]
            h = h1 + 6 + (b2[3] - b2[1]) + 2 * pad
            y0 = y - h
            if y0 < top:
                c.delete(t1, t2)
                break
            x0 = 24 if left else W - 24 - w
            c.move(t1, x0 + pad + (6 if left else 0), y0 + pad)
            c.move(t2, x0 + pad + (6 if left else 0), y0 + pad + h1 + 6)
            fill = self.BUBBLE[src] if trans is not None else PANEL
            edge = self.EDGE[src] if trans is not None else LINE
            r = c.create_rectangle(x0, y0, x0 + w, y, fill=fill, outline=fill)
            # Leuchtkante an der Seite des Sprechers
            bar = c.create_rectangle(x0 if left else x0 + w - 4, y0, x0 + 4 if left else x0 + w, y,
                                     fill=edge, width=0)
            c.tag_lower(bar)
            c.tag_lower(r)
            y = y0 - gap

    def _speaker_bar(self):
        """Wer spricht gerade? Kippen des Mikros wählt die Seite. Darunter die Neigung."""
        c, s, W = self.c, self.s, self.W
        y0, y1 = 376, 450
        for i, lang in enumerate(("de", "en")):
            x0 = 24 if i == 0 else W / 2 + 12
            x1 = W / 2 - 12 if i == 0 else W - 24
            active = s["direction"] == lang
            col = self.EDGE[lang]
            c.create_rectangle(x0, y0, x1, y1, fill=self.BUBBLE[lang] if active else PANEL,
                               outline=col if active else PANEL, width=2)
            arrow = "◀  " if lang == "de" else ""
            arrow_r = "  ▶" if lang == "en" else ""
            c.create_text((x0 + x1) / 2, y0 + 25, text=f"{arrow}{self.LANG_NAME[lang]}{arrow_r}",
                          fill=FG if active else DIM, font=(FONT, 20, "bold"))
            if active:
                if s["loading"]:
                    sub = "einen Moment …" if lang == "de" else "one moment …"
                elif s["thinking"] or s["speaking"]:
                    sub = "übersetze …" if lang == "de" else "translating …"
                elif s["listening"]:
                    sub = "Bitte sprechen" if lang == "de" else "Please speak"
                else:
                    sub = "einen Moment …" if lang == "de" else "one moment …"
            else:
                sub = "Mikro nach links kippen" if lang == "de" else "tilt microphone right"
            c.create_text((x0 + x1) / 2, y0 + 55, text=sub, fill=col if active else DIM, font=(FONT, 14))
        self._tilt_gauge(460, 490)

    def _tilt_gauge(self, y0, y1, full=45):
        """Neigung des Mikros: Mitte = gerade, Striche = Schwelle (±20°).
        Balken wird farbig, sobald weit genug gekippt ist."""
        c, s, W = self.c, self.s, self.W
        x0, x1 = 120, W - 120
        mid = (x0 + x1) / 2
        half = (x1 - x0) / 2
        ym = (y0 + y1) / 2
        c.create_text(24, ym, text="Neigung", fill=DIM, font=(FONT, 13), anchor="w")
        c.create_rectangle(x0, y0 + 6, x1, y1 - 6, fill=BG, outline=LINE, width=1)
        for sign in (-1, 1):  # Schwelle
            xt = mid + sign * half * TILT_ON_DEG / full
            c.create_line(xt, y0, xt, y1, fill=WARN, width=2)
        c.create_line(mid, y0 + 2, mid, y1 - 2, fill=DIM, width=2)
        deg = s["tilt_deg"]
        if deg is None:
            c.create_text(W - 24, ym, text="–", fill=DIM, font=(FONT, 13), anchor="e")
            return
        d = max(-full, min(full, deg))
        xe = mid + half * d / full
        over = abs(deg) >= TILT_ON_DEG
        col = OK if over else ACCENT
        c.create_rectangle(min(mid, xe), y0 + 7, max(mid, xe), y1 - 7, fill=col, width=0)
        c.create_oval(xe - 9, ym - 9, xe + 9, ym + 9, fill=FG if not over else col, outline=BG, width=2)
        c.create_text(W - 24, ym, text=f"{abs(deg)}° {'◀' if deg < 0 else '▶' if deg > 0 else ''}",
                      fill=col if over else FG, font=(FONT, 13, "bold"), anchor="e")

    # ---- Meldung + Tastenleiste (in jedem Zustand) -----------------------------
    def _toast(self):
        """Meldung der Tasten (Material, Pflege, Ladestation) – in jedem Programm sichtbar."""
        c, s, W = self.c, self.s, self.W
        if not s["info"]:
            return
        y1 = self.H - 52
        t = c.create_text(W / 2, y1 - 10, text=s["info"], fill=FG, font=(FONT, 16, "bold"), width=W - 100,
                          anchor="s", justify="center")
        y0 = c.bbox(t)[1] - 10
        c.create_rectangle(24, y0, W - 24, y1, fill=PANEL, width=0)
        c.create_rectangle(24, y0, 32, y1, fill=WARN, width=0)
        self.canvas.tag_raise(t)

    def _key_bar(self):
        """Unten immer sichtbar: Tasten A/B/C wirken in jedem Zustand; rechts der Drehknopf."""
        c, s, W, H = self.c, self.s, self.W, self.H
        y0 = H - 44
        ym = y0 + 22
        c.create_rectangle(0, y0, W, H, fill=BAR, width=0)
        x = 20
        for key, label in KEYS:
            if key == "A" and s["state"] == STANDBY:
                label = "lang drücken: rufen"
            c.create_oval(x, ym - 14, x + 28, ym + 14, outline=DIM, width=2)
            c.create_text(x + 14, ym, text=key, fill=FG, font=(FONT, 13, "bold"))
            t = c.create_text(x + 36, ym, text=label, fill=FG, font=(FONT, 13), anchor="w")
            x = c.bbox(t)[2] + 26
        st = s["state"]
        self._quit_box = None
        if st == AUSWAHL:
            c.create_text(W - 20, ym, text="Knopf drehen: wählen  ·  drücken: starten", fill=ACCENT,
                          font=(FONT, 13, "bold"), anchor="e")
        elif st in TILES:
            t = c.create_text(W - 34, ym, text="✕  Beenden: Knopf drücken", fill=FG, font=(FONT, 13, "bold"),
                              anchor="e")
            b = c.bbox(t)
            box = (b[0] - 14, y0 + 6, W - 20, H - 6)
            c.create_rectangle(*box, fill=SOFT, width=0)
            self.canvas.tag_raise(t)
            self._quit_box = box

    def _click(self, ev):
        """Maus/Touch: Kachel in der Auswahl starten, „Beenden“ im Programm."""
        if not self.on_tile:
            return
        x, y = ev.x / self.k, ev.y / self.k
        if self.s["state"] == AUSWAHL:
            for i, (x0, y0, x1, y1) in enumerate(self._tile_boxes()):
                if x0 <= x <= x1 and y0 <= y <= y1:
                    self.on_tile(i)
        elif self._quit_box:
            x0, y0, x1, y1 = self._quit_box
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.on_tile(None)
