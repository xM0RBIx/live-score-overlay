import tkinter as tk
from tkinter import ttk
import threading
import queue
import os
import sys
import io
import requests
from PIL import Image, ImageTk

REFRESH_SECONDS = 15
SCROLL_SPEED = 2
TRANSPARENT_COLOR = "#010101"
BAR_HEIGHT = 44
LOGO_SIZE = 28
TOAST_LOGO_SIZE = 48
TOAST_SECONDS = 6

LEAGUES = {
    "1. Bundesliga": "ger.1",
    "2. Bundesliga": "ger.2",
    "Champions League": "uefa.champions",
    "DFB-Pokal": "ger.dfb_pokal",
}
BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{}/scoreboard"

# Cache-Ordner im Benutzerprofil (funktioniert auch als exe)
CACHE_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "LiveScoreOverlay", "logo_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


# ---------------------------------------------------------------- Auswahl
def choose_leagues():
    sel = tk.Tk()
    sel.title("Live-Score Overlay")
    sel.resizable(False, False)
    frm = ttk.Frame(sel, padding=20)
    frm.pack()
    ttk.Label(frm, text="Welche Wettbewerbe anzeigen?",
              font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 10))

    vars_ = {}
    for name in LEAGUES:
        v = tk.BooleanVar(value=True)
        vars_[name] = v
        ttk.Checkbutton(frm, text=name, variable=v).pack(anchor="w", pady=2)

    result = {"codes": None}

    def start():
        codes = [LEAGUES[n] for n, v in vars_.items() if v.get()]
        if not codes:
            return
        result["codes"] = codes
        sel.destroy()

    ttk.Button(frm, text="Overlay starten", command=start).pack(pady=(15, 0), fill="x")
    sel.mainloop()
    return result["codes"]


codes = choose_leagues()
if not codes:
    sys.exit(0)

# ---------------------------------------------------------------- Daten
lock = threading.Lock()
games_state = []
goal_queue = queue.Queue()
last_scores = {}
pil_cache = {}


def get_logo_image(url):
    if not url:
        return None
    if url in pil_cache:
        return pil_cache[url]
    fname = os.path.join(CACHE_DIR, url.split("/")[-1].split("?")[0])
    try:
        if os.path.exists(fname):
            img = Image.open(fname).convert("RGBA")
        else:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            with open(fname, "wb") as f:
                f.write(r.content)
            img = Image.open(io.BytesIO(r.content)).convert("RGBA")
    except Exception:
        img = None
    pil_cache[url] = img
    return img


def fetch_live():
    games = []
    for code in codes:
        try:
            r = requests.get(BASE.format(code), timeout=15)
            r.raise_for_status()
            events = r.json().get("events", [])
        except Exception:
            continue
        for ev in events:
            if ev["status"]["type"]["state"] != "in":
                continue
            comp = ev["competitions"][0]
            teams = {c["homeAway"]: c for c in comp["competitors"]}
            h, a = teams["home"], teams["away"]
            games.append({
                "id": ev["id"],
                "home": h["team"].get("shortDisplayName", "?"),
                "away": a["team"].get("shortDisplayName", "?"),
                "home_logo": h["team"].get("logo", ""),
                "away_logo": a["team"].get("logo", ""),
                "hs": int(h.get("score", 0) or 0),
                "as": int(a.get("score", 0) or 0),
                "clock": ev["status"].get("displayClock", ""),
            })
    return games


def updater():
    global games_state
    first = True
    while True:
        try:
            games = fetch_live()
            for g in games:
                get_logo_image(g["home_logo"])
                get_logo_image(g["away_logo"])
                prev = last_scores.get(g["id"])
                cur = (g["hs"], g["as"])
                if prev is not None and not first and cur != prev:
                    if g["hs"] > prev[0]:
                        goal_queue.put((g, True))
                    if g["as"] > prev[1]:
                        goal_queue.put((g, False))
                last_scores[g["id"]] = cur
            first = False
            with lock:
                games_state = games
        except Exception:
            pass
        threading.Event().wait(REFRESH_SECONDS)


# ---------------------------------------------------------------- GUI
root = tk.Tk()
root.overrideredirect(True)
root.attributes("-topmost", True)
root.config(bg=TRANSPARENT_COLOR)
root.attributes("-transparentcolor", TRANSPARENT_COLOR)
sw = root.winfo_screenwidth()
root.geometry(f"{sw}x{BAR_HEIGHT}+0+0")

canvas = tk.Canvas(root, width=sw, height=BAR_HEIGHT, bg=TRANSPARENT_COLOR,
                   highlightthickness=0)
canvas.pack()

tk_cache = {}


def tk_logo(url, size):
    key = (url, size)
    if key in tk_cache:
        return tk_cache[key]
    img = pil_cache.get(url)
    if img is None:
        return None
    ph = ImageTk.PhotoImage(img.resize((size, size), Image.LANCZOS))
    tk_cache[key] = ph
    return ph


offset = {"x": sw}
last_sig = {"v": None}
total_width = {"v": 0}


def rebuild():
    canvas.delete("all")
    with lock:
        games = list(games_state)
    if not games:
        total_width["v"] = 0
        return
    x = 0
    font = ("Segoe UI", 14, "bold")
    cy = BAR_HEIGHT // 2
    for g in games:
        lh = tk_logo(g["home_logo"], LOGO_SIZE)
        la = tk_logo(g["away_logo"], LOGO_SIZE)
        if lh:
            canvas.create_image(x, cy, image=lh, anchor="w")
            x += LOGO_SIZE + 6
        t = canvas.create_text(x, cy, text=f"{g['home']} {g['hs']}:{g['as']} {g['away']}",
                               font=font, fill="white", anchor="w")
        x = canvas.bbox(t)[2] + 6
        if la:
            canvas.create_image(x, cy, image=la, anchor="w")
            x += LOGO_SIZE + 6
        t = canvas.create_text(x, cy, text=f"({g['clock']})", font=("Segoe UI", 11),
                               fill="#ffd54f", anchor="w")
        x = canvas.bbox(t)[2] + 60
    total_width["v"] = x


def animate():
    with lock:
        sig = tuple((g["id"], g["hs"], g["as"], g["clock"]) for g in games_state)
    if sig != last_sig["v"]:
        last_sig["v"] = sig
        rebuild()
    if total_width["v"] > 0:
        offset["x"] -= SCROLL_SPEED
        if offset["x"] < -total_width["v"]:
            offset["x"] = sw
        canvas.delete("shadow")
        canvas.move("all", 0, 0)
    canvas.xview_moveto(0)
    canvas.configure(scrollregion=(0, 0, sw, BAR_HEIGHT))
    canvas.scan_dragto(int(offset["x"]), 0, gain=1)
    root.after(30, animate)


toast_busy = {"v": False}


def show_toast(g, home_scored):
    toast_busy["v"] = True
    bg = "#1b5e20"
    t = tk.Toplevel(root)
    t.overrideredirect(True)
    t.attributes("-topmost", True)
    t.config(bg=bg)

    scorer = g["home"] if home_scored else g["away"]
    tk.Label(t, text=f"⚽ TOR! {scorer}", font=("Segoe UI", 13, "bold"),
             fg="white", bg=bg, pady=6).pack()

    row = tk.Frame(t, bg=bg)
    row.pack(padx=18, pady=(0, 10))
    lh = tk_logo(g["home_logo"], TOAST_LOGO_SIZE)
    la = tk_logo(g["away_logo"], TOAST_LOGO_SIZE)
    if lh:
        tk.Label(row, image=lh, bg=bg).pack(side="left", padx=8)
    tk.Label(row, text=f"{g['hs']}:{g['as']}", font=("Segoe UI", 22, "bold"),
             fg="white", bg=bg).pack(side="left", padx=8)
    if la:
        tk.Label(row, image=la, bg=bg).pack(side="left", padx=8)
    tk.Label(t, text=f"{g['home']} - {g['away']}  ({g['clock']})",
             font=("Segoe UI", 10), fg="#c8e6c9", bg=bg, pady=4).pack()

    t.update_idletasks()
    w = t.winfo_width()
    t.geometry(f"+{(sw - w) // 2}+{BAR_HEIGHT + 10}")

    def close():
        t.destroy()
        toast_busy["v"] = False

    t.after(TOAST_SECONDS * 1000, close)


def check_goals():
    if not toast_busy["v"]:
        try:
            g, home_scored = goal_queue.get_nowait()
            show_toast(g, home_scored)
        except queue.Empty:
            pass
    root.after(500, check_goals)


root.bind("<Button-3>", lambda e: root.destroy())  # Rechtsklick beendet

threading.Thread(target=updater, daemon=True).start()
animate()
check_goals()
root.mainloop()