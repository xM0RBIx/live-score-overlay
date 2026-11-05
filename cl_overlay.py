import tkinter as tk
from tkinter import colorchooser
import ctypes
import threading
import time
import json
import os
import sys
import queue
import io
import traceback
from datetime import datetime

if getattr(sys, "frozen", False):
    BASIS = os.path.dirname(sys.executable)
else:
    BASIS = os.path.dirname(os.path.abspath(__file__))

LOG_DATEI = os.path.join(BASIS, "overlay_error.log")
SETTINGS_DATEI = os.path.join(BASIS, "overlay_settings.json")
LOGO_ORDNER = os.path.join(BASIS, "logos")


def log_fehler():
    try:
        with open(LOG_DATEI, "a", encoding="utf-8") as f:
            f.write(datetime.now().isoformat() + "\n")
            f.write(traceback.format_exc() + "\n")
    except Exception:
        pass


try:
    import requests
    import pystray
    from PIL import Image, ImageDraw, ImageTk
except Exception:
    log_fehler()
    raise

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

LIGAS = {
    "1. Bundesliga": "ger.1",
    "2. Bundesliga": "ger.2",
    "Champions League": "uefa.champions",
    "DFB-Pokal": "ger.dfb_pokal",
}

ALLE = "Alle Vereine"
REFRESH_SEKUNDEN = 30
HINWEIS = "Beenden: Tray-Icon (unten rechts)"
ABSTAND_SPIEL = 50

BG = "#12141a"
CARD = "#1c1f28"
ACCENT = "#3b82f6"
TEXT = "#e8eaf0"
MUTED = "#8a90a2"
FARBEN = ["#ffffff", "#ffd60a", "#30d158", "#0a84ff",
          "#ff453a", "#ff9f0a", "#bf5af2", "#64d2ff"]

STANDARD = {
    "ligen": {n: True for n in LIGAS},
    "beendete": {n: False for n in LIGAS},
    "anstehende": {n: False for n in LIGAS},
    "favorit": {n: ALLE for n in LIGAS},
    "logos": True,
    "schriftgroesse": 16,
    "farbe": "#ffffff",
    "deckkraft": 85,
    "tempo": 2,
}


def lade_settings():
    s = json.loads(json.dumps(STANDARD))
    try:
        with open(SETTINGS_DATEI, "r", encoding="utf-8") as f:
            d = json.load(f)
        for k, v in d.items():
            if isinstance(v, dict) and isinstance(s.get(k), dict):
                s[k].update(v)
            else:
                s[k] = v
    except Exception:
        pass
    return s


def speichere_settings(s):
    try:
        with open(SETTINGS_DATEI, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except Exception:
        log_fehler()


# ---------------- Daten ----------------
def hole_teams(code):
    """Alle Teams einer Liga: {name: logo_url}"""
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/teams"
    out = {}
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        for sp in data.get("sports", []):
            for lg in sp.get("leagues", []):
                for t in lg.get("teams", []):
                    tm = t.get("team", {})
                    name = tm.get("shortDisplayName") or tm.get("displayName")
                    logo = ""
                    logos = tm.get("logos") or []
                    if logos:
                        logo = logos[0].get("href", "")
                    if name:
                        out[name] = logo
    except Exception:
        log_fehler()
    return out


def hole_spiele(code):
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard"
    out = []
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        for ev in r.json().get("events", []):
            comp = ev["competitions"][0]
            status = comp["status"]["type"]
            state = status.get("state", "")
            teams = comp["competitors"]
            heim = next(t for t in teams if t["homeAway"] == "home")
            gast = next(t for t in teams if t["homeAway"] == "away")
            ht, gt = heim["team"], gast["team"]
            hn = ht.get("shortDisplayName", ht.get("displayName", "?"))
            gn = gt.get("shortDisplayName", gt.get("displayName", "?"))
            if state == "pre":
                try:
                    dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
                    mitte = dt.astimezone().strftime("%d.%m. %H:%M")
                except Exception:
                    mitte = "bald"
                zusatz = ""
            elif state == "in":
                mitte = f"{heim.get('score', '0')}:{gast.get('score', '0')}"
                zusatz = status.get("shortDetail", "LIVE")
            else:
                mitte = f"{heim.get('score', '0')}:{gast.get('score', '0')}"
                zusatz = "Ende"
            out.append({
                "state": state, "heim": hn, "gast": gn,
                "heim_logo": ht.get("logo", ""), "gast_logo": gt.get("logo", ""),
                "mitte": mitte, "zusatz": zusatz,
            })
    except Exception:
        log_fehler()
    return out


# ---------------- Logos ----------------
class LogoCache:
    def __init__(self):
        self.roh = {}      # url -> PIL Image
        self.tk = {}       # (url, groesse) -> PhotoImage
        self.laeuft = set()
        os.makedirs(LOGO_ORDNER, exist_ok=True)

    def _datei(self, url):
        name = "".join(c if c.isalnum() else "_" for c in url)[-80:]
        return os.path.join(LOGO_ORDNER, name + ".png")

    def laden(self, url):
        """Blockierend, im Hintergrund-Thread aufrufen."""
        if not url or url in self.roh:
            return
        pfad = self._datei(url)
        try:
            if os.path.exists(pfad):
                img = Image.open(pfad).convert("RGBA")
            else:
                r = requests.get(url, timeout=10)
                r.raise_for_status()
                img = Image.open(io.BytesIO(r.content)).convert("RGBA")
                img.save(pfad)
            self.roh[url] = img
        except Exception:
            log_fehler()

    def holen(self, url, groesse):
        """Nur im GUI-Thread. Gibt PhotoImage oder None."""
        if not url or url not in self.roh:
            return None
        key = (url, groesse)
        if key not in self.tk:
            img = self.roh[url].copy()
            img.thumbnail((groesse, groesse), Image.LANCZOS)
            self.tk[key] = ImageTk.PhotoImage(img)
        return self.tk[key]


def tray_bild():
    img = Image.new("RGB", (64, 64), ACCENT)
    d = ImageDraw.Draw(img)
    d.ellipse((14, 14, 50, 50), outline="white", width=4)
    d.rectangle((29, 8, 35, 56), fill="white")
    return img


def klick_durchlaessig(fenster):
    try:
        fenster.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(fenster.winfo_id())
        GWL_EXSTYLE = -20
        stil = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        stil |= (0x00080000 | 0x00000020 | 0x00000080 | 0x08000000)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, stil)
    except Exception:
        log_fehler()


# ---------------- Eigene Vereinsauswahl mit Logos ----------------
class VereinsAuswahl(tk.Frame):
    def __init__(self, master, app, liga, variable, command):
        super().__init__(master, bg=CARD)
        self.app = app
        self.liga = liga
        self.var = variable
        self.command = command
        self.popup = None
        self.bilder = []

        self.knopf = tk.Label(self, bg=BG, fg=TEXT, anchor="w", padx=6, pady=3,
                              font=("Segoe UI", 9), cursor="hand2", compound="left",
                              width=200, relief="flat")
        self.knopf.pack(fill="x")
        self.knopf.bind("<Button-1>", lambda e: self.oeffnen())
        self.aktualisiere_knopf()

    def logo_fuer(self, name):
        url = self.app.team_logos.get(self.liga, {}).get(name, "")
        return self.app.logos.holen(url, 18)

    def aktualisiere_knopf(self):
        name = self.var.get() or ALLE
        img = self.logo_fuer(name) if name != ALLE else None
        self.knopf.config(text="  " + name + "   v", image=img if img else "")
        self.knopf.image = img

    def oeffnen(self):
        if self.popup is not None:
            self.schliessen()
            return
        p = tk.Toplevel(self)
        self.popup = p
        p.overrideredirect(True)
        p.attributes("-topmost", True)
        p.configure(bg=ACCENT)
        x = self.knopf.winfo_rootx()
        y = self.knopf.winfo_rooty() + self.knopf.winfo_height()
        breite = max(self.knopf.winfo_width(), 240)
        p.geometry(f"{breite}x260+{x}+{y}")

        innen = tk.Frame(p, bg=BG)
        innen.pack(fill="both", expand=True, padx=1, pady=1)
        canvas = tk.Canvas(innen, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(innen, orient="vertical", command=canvas.yview)
        liste = tk.Frame(canvas, bg=BG)
        liste.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=liste, anchor="nw", width=breite - 20)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def mausrad(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")
        p.bind("<MouseWheel>", mausrad)
        canvas.bind("<MouseWheel>", mausrad)
        liste.bind("<MouseWheel>", mausrad)

        self.bilder = []
        namen = [ALLE] + self.app.vereine.get(self.liga, [])
        for n in namen:
            img = self.logo_fuer(n) if n != ALLE else None
            if img:
                self.bilder.append(img)
            zeile = tk.Label(liste, text="  " + n, image=img if img else "",
                             compound="left", bg=BG, fg=TEXT, anchor="w",
                             padx=6, pady=2, font=("Segoe UI", 10), cursor="hand2")
            zeile.pack(fill="x")
            zeile.bind("<Button-1>", lambda e, nn=n: self.waehlen(nn))
            zeile.bind("<Enter>", lambda e, z=zeile: z.config(bg="#2b2f3b"))
            zeile.bind("<Leave>", lambda e, z=zeile: z.config(bg=BG))
            zeile.bind("<MouseWheel>", mausrad)

        p.bind("<FocusOut>", lambda e: self.after(150, self.schliessen))
        p.focus_set()

    def waehlen(self, name):
        self.var.set(name)
        self.schliessen()
        self.aktualisiere_knopf()
        self.command()

    def schliessen(self):
        if self.popup is not None:
            try:
                self.popup.destroy()
            except Exception:
                pass
            self.popup = None


# ---------------- App ----------------
class App:
    def __init__(self):
        self.s = lade_settings()
        self.q = queue.Queue()
        self.sichtbar = True
        self.spiele = []          # Liste von (liga, [spiele])
        self.leer_text = "Lade Spiele..."
        self.offset = 0.0
        self.breite = 100
        self.laeuft = True
        self.einst = None
        self.abruf_aktiv = False
        self.vereine = {n: [] for n in LIGAS}
        self.team_logos = {n: {} for n in LIGAS}
        self.auswahlen = {}
        self.logos = LogoCache()
        self.overlay_bilder = []
        self.teams_geladen = set()

         self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.root.geometry("1x1+-100+-100")
        self.root.overrideredirect(True)

        self.ov = tk.Toplevel(self.root)
        self.ov.overrideredirect(True)
        self.ov.attributes("-topmost", True)
        self.sw = self.ov.winfo_screenwidth()
        self.hoehe = 40
        self.ov.geometry(f"{self.sw}x{self.hoehe}+0+0")
        self.ov.configure(bg="black")
        self.canvas = tk.Canvas(self.ov, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.wende_an()
        self.zeichne()
        self.ov.update_idletasks()
        self.ov.deiconify()
        self.ov.lift()
        klick_durchlaessig(self.ov)

        menue = pystray.Menu(
            pystray.MenuItem("Einstellungen", lambda i, it: self.q.put("einst"), default=True),
            pystray.MenuItem("Overlay anzeigen/ausblenden", lambda i, it: self.q.put("toggle")),
            pystray.MenuItem("Jetzt aktualisieren", lambda i, it: self.q.put("refresh")),
            pystray.MenuItem("Beenden", lambda i, it: self.q.put("ende")),
        )
        self.tray = pystray.Icon("LiveScoreOverlay", tray_bild(), "Live-Score Overlay", menue)
        threading.Thread(target=self.tray.run, daemon=True).start()

        threading.Thread(target=self.teams_loop, daemon=True).start()
        threading.Thread(target=self.refresh_loop, daemon=True).start()
        self.root.after(50, self.queue_pruefen)
        self.root.after(30, self.scroll)
        self.root.mainloop()

    # ---------- Overlay zeichnen ----------
    def wende_an(self):
        try:
            self.ov.attributes("-transparentcolor", "black")
        except Exception:
            pass
        self.ov.attributes("-alpha", self.s["deckkraft"] / 100.0)
        gr = int(self.s["schriftgroesse"])
        self.hoehe = max(34, gr * 2 + 10)
        self.ov.geometry(f"{self.sw}x{self.hoehe}+0+0")

    def zeichne(self):
        """Baut den kompletten Laufband-Inhalt neu auf."""
        c = self.canvas
        c.delete("all")
        self.overlay_bilder = []
        gr = int(self.s["schriftgroesse"])
        font = ("Segoe UI", gr, "bold")
        font_klein = ("Segoe UI", max(8, gr - 5))
        farbe = self.s["farbe"]
        logo_gr = int(gr * 1.7)
        mitte_y = self.hoehe // 2 - 3
        x = 0

        def text(t, f=font, col=farbe, abstand=8):
            nonlocal x
            i = c.create_text(x, mitte_y, text=t, anchor="w", fill=col, font=f,
                              tags="band")
            bb = c.bbox(i)
            x = bb[2] + abstand

        def logo(url):
            nonlocal x
            if not self.s["logos"]:
                return
            img = self.logos.holen(url, logo_gr)
            if img:
                self.overlay_bilder.append(img)
                c.create_image(x, mitte_y, image=img, anchor="w", tags="band")
                x += img.width() + 6

        if not self.spiele:
            text(self.leer_text)
        else:
            for liga, liste in self.spiele:
                text(f"[{liga}]", font_klein, MUTED, 14)
                for sp in liste:
                    logo(sp["heim_logo"])
                    text(sp["heim"], abstand=10)
                    text(sp["mitte"], abstand=10)
                    logo(sp["gast_logo"])
                    text(sp["gast"], abstand=8)
                    if sp["zusatz"]:
                        text(f"({sp['zusatz']})", font_klein, MUTED, 8)
                    x += ABSTAND_SPIEL
                x += 20

        self.breite = max(x, 100)
        self.hint = c.create_text(self.sw - 8, self.hoehe - 2, text=HINWEIS,
                                  anchor="se", fill=MUTED, font=("Segoe UI", 7))
        c.move("band", self.sw - self.offset, 0)
        self.band_x = self.sw - self.offset

    def scroll(self):
        if not self.laeuft:
            return
        try:
            self.offset += self.s["tempo"]
            if self.offset > self.sw + self.breite:
                self.offset = 0
            neu = self.sw - self.offset
            self.canvas.move("band", neu - self.band_x, 0)
            self.band_x = neu
        except Exception:
            log_fehler()
        self.root.after(20, self.scroll)

    # ---------- Daten ----------
    def abrufen(self):
        if self.abruf_aktiv:
            return
        self.abruf_aktiv = True
        try:
            ergebnis = []
            for name, code in LIGAS.items():
                if not self.s["ligen"].get(name, False):
                    continue
                alle = hole_spiele(code)

                # Teams aus Spielen ergaenzen
                bekannt = set(self.vereine.get(name, []))
                for sp in alle:
                    bekannt.add(sp["heim"])
                    bekannt.add(sp["gast"])
                    self.team_logos[name].setdefault(sp["heim"], sp["heim_logo"])
                    self.team_logos[name].setdefault(sp["gast"], sp["gast_logo"])
                fav = self.s["favorit"].get(name, ALLE)
                if fav != ALLE:
                    bekannt.add(fav)
                self.vereine[name] = sorted(bekannt)

                gefiltert = []
                for sp in alle:
                    if self.s["beendete"].get(name) and sp["state"] == "post":
                        continue
                    if self.s["anstehende"].get(name) and sp["state"] == "pre":
                        continue
                    if fav != ALLE and fav not in (sp["heim"], sp["gast"]):
                        continue
                    gefiltert.append(sp)

                # Logos fuer Overlay vorladen
                if self.s["logos"]:
                    for sp in gefiltert:
                        self.logos.laden(sp["heim_logo"])
                        self.logos.laden(sp["gast_logo"])
                if gefiltert:
                    ergebnis.append((name, gefiltert))
            self.q.put(("spiele", ergebnis))
            self.q.put(("vereine", None))
        except Exception:
            log_fehler()
        finally:
            self.abruf_aktiv = False

    def teams_laden(self):
        """Komplette Teamlisten + Logos je Liga (einmal pro Liga)."""
        for name, code in LIGAS.items():
            if name in self.teams_geladen:
                continue
            teams = hole_teams(code)
            if teams:
                self.teams_geladen.add(name)
                self.team_logos[name].update(
                    {k: v for k, v in teams.items() if v})
                bekannt = set(self.vereine.get(name, [])) | set(teams.keys())
                fav = self.s["favorit"].get(name, ALLE)
                if fav != ALLE:
                    bekannt.add(fav)
                self.vereine[name] = sorted(bekannt)
                for url in teams.values():
                    self.logos.laden(url)
                self.q.put(("vereine", name))

    def teams_loop(self):
        while self.laeuft:
            self.teams_laden()
            if len(self.teams_geladen) >= len(LIGAS):
                return
            for _ in range(60):
                if not self.laeuft:
                    return
                time.sleep(0.5)

    def refresh_loop(self):
        while self.laeuft:
            self.abrufen()
            for _ in range(REFRESH_SEKUNDEN * 2):
                if not self.laeuft:
                    return
                time.sleep(0.5)

    def sofort_abrufen(self):
        threading.Thread(target=self.abrufen, daemon=True).start()

    def auswahl_aktualisieren(self):
        for a in self.auswahlen.values():
            try:
                a.aktualisiere_knopf()
            except Exception:
                pass

    def queue_pruefen(self):
        try:
            while True:
                m = self.q.get_nowait()
                if isinstance(m, tuple) and m[0] == "spiele":
                    self.spiele = m[1]
                    self.leer_text = "Keine Spiele aktuell"
                    self.zeichne()
                elif isinstance(m, tuple) and m[0] == "vereine":
                    if self.einst is not None:
                        self.auswahl_aktualisieren()
                elif m == "toggle":
                    self.sichtbar = not self.sichtbar
                    if self.sichtbar:
                        self.ov.deiconify()
                        self.ov.attributes("-topmost", True)
                        klick_durchlaessig(self.ov)
                    else:
                        self.ov.withdraw()
                elif m == "refresh":
                    self.sofort_abrufen()
                elif m == "einst":
                    self.zeige_einstellungen()
                elif m == "ende":
                    self.beenden()
                    return
        except queue.Empty:
            pass
        if self.laeuft:
            self.root.after(50, self.queue_pruefen)

    def beenden(self):
        self.laeuft = False
        try:
            self.tray.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    # ---------- Einstellungen (einmal aufgebaut) ----------
    def zeige_einstellungen(self):
        if self.einst is not None:
            try:
                self.einst.deiconify()
                self.einst.lift()
                self.einst.focus_force()
                return
            except Exception:
                self.einst = None

          w = tk.Toplevel(self.root)
        self.einst = w
        w.title("Live-Score Overlay - Einstellungen")
        w.configure(bg=BG)
        w.resizable(True, True)
        w.minsize(420, 400)
        w.attributes("-topmost", True)

        def schliessen():
            speichere_settings(self.s)
            for a in self.auswahlen.values():
                a.schliessen()
            self.einst = None
            self.auswahlen = {}
            w.destroy()

        w.protocol("WM_DELETE_WINDOW", schliessen)

        # scrollbarer Bereich, damit die Breite/Hoehe frei anpassbar ist
        aussen = tk.Frame(w, bg=BG)
        aussen.pack(fill="both", expand=True)
        sc_canvas = tk.Canvas(aussen, bg=BG, highlightthickness=0)
        sc_bar = tk.Scrollbar(aussen, orient="vertical", command=sc_canvas.yview)
        sc_canvas.configure(yscrollcommand=sc_bar.set)
        sc_bar.pack(side="right", fill="y")
        sc_canvas.pack(side="left", fill="both", expand=True)
        w_inhalt = tk.Frame(sc_canvas, bg=BG)
        fenster_id = sc_canvas.create_window((0, 0), window=w_inhalt, anchor="nw")
        w_inhalt.bind("<Configure>",
                      lambda e: sc_canvas.configure(scrollregion=sc_canvas.bbox("all")))
        sc_canvas.bind("<Configure>",
                       lambda e: sc_canvas.itemconfig(fenster_id, width=e.width))
        sc_canvas.bind_all("<MouseWheel>",
                           lambda e: sc_canvas.yview_scroll(int(-e.delta / 120), "units"))
        w_orig = w
        w = w_inhalt  # ab hier werden alle Elemente in den scrollbaren Bereich gepackt

        tk.Label(w, text="Einstellungen", bg=BG, fg=TEXT,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=24, pady=(18, 2))
        tk.Label(w, text="Aenderungen gelten sofort.", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=24, pady=(0, 10))

        card = tk.Frame(w, bg=CARD)
        card.pack(fill="x", padx=24, pady=4)
        tk.Label(card, text="Wettbewerbe", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=14, pady=(10, 4))

        self.vars_liga = {}
        self.vars_end = {}
        self.vars_anst = {}
        self.vars_fav = {}

        def liga_geaendert(*_):
            for n in LIGAS:
                self.s["ligen"][n] = bool(self.vars_liga[n].get())
                self.s["beendete"][n] = bool(self.vars_end[n].get())
                self.s["anstehende"][n] = bool(self.vars_anst[n].get())
                self.s["favorit"][n] = self.vars_fav[n].get() or ALLE
            speichere_settings(self.s)
            self.sofort_abrufen()

        for n in LIGAS:
            block = tk.Frame(card, bg=CARD)
            block.pack(fill="x", padx=14, pady=(2, 8))

            v1 = tk.BooleanVar(value=self.s["ligen"].get(n, True))
            v2 = tk.BooleanVar(value=self.s["beendete"].get(n, False))
            v3 = tk.BooleanVar(value=self.s["anstehende"].get(n, False))
            v4 = tk.StringVar(value=self.s["favorit"].get(n, ALLE))
            self.vars_liga[n] = v1
            self.vars_end[n] = v2
            self.vars_anst[n] = v3
            self.vars_fav[n] = v4

            tk.Checkbutton(block, text=n, variable=v1, command=liga_geaendert,
                           bg=CARD, fg=TEXT, selectcolor=BG, activebackground=CARD,
                           activeforeground=TEXT, font=("Segoe UI", 10, "bold")
                           ).grid(row=0, column=0, columnspan=2, sticky="w")
            tk.Checkbutton(block, text="Beendete ausblenden", variable=v2,
                           command=liga_geaendert, bg=CARD, fg=MUTED, selectcolor=BG,
                           activebackground=CARD, activeforeground=TEXT,
                           font=("Segoe UI", 9)
                           ).grid(row=1, column=0, sticky="w", padx=(18, 8))
            tk.Checkbutton(block, text="Anstehende ausblenden", variable=v3,
                           command=liga_geaendert, bg=CARD, fg=MUTED, selectcolor=BG,
                           activebackground=CARD, activeforeground=TEXT,
                           font=("Segoe UI", 9)
                           ).grid(row=1, column=1, sticky="w")
            tk.Label(block, text="Nur dieser Verein:", bg=CARD, fg=MUTED,
                     font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w",
                                                padx=(18, 8), pady=(3, 0))
            auswahl = VereinsAuswahl(block, self, n, v4, liga_geaendert)
            auswahl.grid(row=2, column=1, sticky="we", pady=(3, 0))
            self.auswahlen[n] = auswahl

        # ---- Darstellung
        card2 = tk.Frame(w, bg=CARD)
        card2.pack(fill="x", padx=24, pady=8)
        tk.Label(card2, text="Darstellung", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=14, pady=(10, 4))

        var_logos = tk.BooleanVar(value=self.s.get("logos", True))

        def logos_geaendert():
            self.s["logos"] = bool(var_logos.get())
            speichere_settings(self.s)
            self.sofort_abrufen()
            self.zeichne()

        tk.Checkbutton(card2, text="Logos im Overlay anzeigen", variable=var_logos,
                       command=logos_geaendert, bg=CARD, fg=TEXT, selectcolor=BG,
                       activebackground=CARD, activeforeground=TEXT,
                       font=("Segoe UI", 10)).pack(anchor="w", padx=14, pady=(0, 6))

        farbe = {"wert": self.s["farbe"]}
        vorschau = tk.Label(w, text="Vorschau: Bayern 2:1 Dortmund", bg="#000000",
                            fg=self.s["farbe"], pady=8)

        def vorschau_update():
            vorschau.config(fg=farbe["wert"],
                            font=("Segoe UI", int(self.s["schriftgroesse"]), "bold"))

        def uebernehmen(neu_zeichnen=True):
            self.s["farbe"] = farbe["wert"]
            self.wende_an()
            if neu_zeichnen:
                self.zeichne()
            vorschau_update()
            speichere_settings(self.s)

        def regler(label, key, von, bis, einheit, zeichnen):
            zeile = tk.Frame(card2, bg=CARD)
            zeile.pack(fill="x", padx=14, pady=3)
            tk.Label(zeile, text=label, bg=CARD, fg=TEXT, width=14, anchor="w",
                     font=("Segoe UI", 10)).pack(side="left")
            wert_lbl = tk.Label(zeile, text=f"{self.s[key]}{einheit}", bg=CARD,
                                fg=MUTED, width=7, font=("Segoe UI", 10))
            wert_lbl.pack(side="right")

            def bei_aenderung(val):
                self.s[key] = int(float(val))
                wert_lbl.config(text=f"{self.s[key]}{einheit}")
                self.wende_an()
                if zeichnen:
                    self.zeichne()
                vorschau_update()

            sk = tk.Scale(zeile, from_=von, to=bis, orient="horizontal",
                          showvalue=False, bg=CARD, fg=TEXT, troughcolor=BG,
                          highlightthickness=0, bd=0, length=200,
                          command=bei_aenderung)
            sk.set(self.s[key])
            sk.pack(side="left", padx=6)
            sk.bind("<ButtonRelease-1>", lambda e: speichere_settings(self.s))

        regler("Schriftgroesse", "schriftgroesse", 10, 40, " pt", True)
        regler("Deckkraft", "deckkraft", 30, 100, " %", False)
        regler("Scrolltempo", "tempo", 1, 6, "", False)

        tk.Label(card2, text="Schriftfarbe", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10)).pack(anchor="w", padx=14, pady=(8, 4))
        pal = tk.Frame(card2, bg=CARD)
        pal.pack(anchor="w", padx=14)

        def setze_farbe(c):
            farbe["wert"] = c
            uebernehmen()

        def eigene_farbe():
            c = colorchooser.askcolor(color=farbe["wert"], parent=w)[1]
            if c:
                setze_farbe(c)

        for f in FARBEN:
            b = tk.Label(pal, bg=f, width=3, height=1, cursor="hand2")
            b.pack(side="left", padx=2)
            b.bind("<Button-1>", lambda e, c=f: setze_farbe(c))
        eig = tk.Label(pal, text=" Eigene... ", bg="#2b2f3b", fg=TEXT,
                       cursor="hand2", font=("Segoe UI", 9))
        eig.pack(side="left", padx=(8, 0))
        eig.bind("<Button-1>", lambda e: eigene_farbe())
        tk.Frame(card2, bg=CARD, height=10).pack()

        vorschau.pack(fill="x", padx=24, pady=(8, 0))
        vorschau_update()

        ok = tk.Label(w, text="FERTIG", bg=ACCENT, fg="white",
                      font=("Segoe UI", 11, "bold"), pady=10, cursor="hand2")
        ok.pack(fill="x", padx=24, pady=16)
        ok.bind("<Button-1>", lambda e: schliessen())
        ok.bind("<Enter>", lambda e: ok.config(bg="#5b9bff"))
        ok.bind("<Leave>", lambda e: ok.config(bg=ACCENT))

        w.update_idletasks()
        x = (w.winfo_screenwidth() - w.winfo_width()) // 2
        y = max(0, (w.winfo_screenheight() - w.winfo_height()) // 2 - 20)
        w.geometry(f"+{x}+{y}")


if __name__ == "__main__":
    try:
        App()
    except Exception:
        log_fehler()
        raise
