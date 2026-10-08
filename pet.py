"""
Clawd - a tiny pixel Claude critter that lives on the edges of your screen.

- Hides behind screen edges (you can see its little hands holding on),
  shuffles along the edges, peeks, pops up, waves, naps.
- Hover it to pet it (happy eyes + hearts). Click it to make it hop.
- When Claude Code needs your permission it drops down from the top of the
  screen (by the camera) and dangles there until you deal with it.
- Right-click for a menu.

Controlled over localhost TCP by notify.py (called from Claude Code hooks).
"""
import collections
import ctypes
import math
import os
import queue
import random
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

PORT = 47863
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "pet.log")

KEY = "#010101"  # transparent colour key
C = {
    "O": "#D97757",  # claude orange
    "D": "#B4593B",  # shade
    "L": "#ECA587",  # highlight
    "K": "#1E1915",  # eyes
    "W": "#FFFFFF",
    "P": "#F08F86",  # blush
    "R": "#E5484D",  # alert red
    "H": "#E8546A",  # heart
    "S": "#F5C451",  # sparkle
    "Z": "#A9B0C6",  # sleepy z
    "B": "#7FC8F8",  # sweat
    "T": "#2B211C",  # text / bubble outline
}

BODY = [  # 10 wide, sits at sprite columns 2..11
    "LLOOOOOOOO",
    "LOOOOOOOOD",
    "OOOOOOOOOD",
    "OOOOOOOOOD",
    "OOOOOOOOOD",
    "OOOOOOOOOD",
    "OOOOOOOOOD",
    "DDDDDDDDDD",
]
HEART = [".X.X.", "XXXXX", "XXXXX", ".XXX.", "..X.."]
SPARK = ["..X..", "..X..", "XXXXX", "..X..", "..X.."]
ZED = ["XXXX", "..X.", ".X..", "XXXX"]
DROP = [".X.", "XXX", ".X."]

LINES_CLICK = ["hi!", "*beep*", "hehe", "pat pat", "I'm helping!", "boop", "need a hand?", "hey :)", "clawd!"]
LINES_POP = ["hi :)", "just checking", "still here!", "*yawn*", "o/", "wheee"]
LINES_GRAB = ["hey!", "wahh!", "put me down!", "eek!", "hehe stop"]
LINES_DONE = ["all done!", "done :)", "ta-da!", "finished!", "your turn!"]


def log(*a):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- win32 bits
def set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]


def work_area():
    r = RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
    return r.l, r.t, r.r, r.b


def focus_claude():
    """Bring the Claude window (desktop app or a terminal running Claude Code) to the front."""
    try:
        user32 = ctypes.windll.user32
        exact, loose = [], []
        proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(h, _):
            if user32.IsWindowVisible(h):
                n = user32.GetWindowTextLengthW(h)
                if n:
                    b = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(h, b, n + 1)
                    t = b.value
                    if t == "Claude":
                        exact.append(h)
                    elif "Claude" in t and "clawd-pet" not in t:
                        loose.append(h)
            return True

        user32.EnumWindows(proto(cb), 0)
        hits = exact or loose
        if hits:
            h = hits[0]
            if user32.IsIconic(h):
                user32.ShowWindow(h, 9)
            user32.SetForegroundWindow(h)
        else:
            open_claude()
    except Exception as e:
        log("focus_claude failed", e)


def claude_running():
    """True if the Claude desktop app or Claude Code (both claude.exe) is running."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq claude.exe", "/NH"], capture_output=True,
                             text=True, timeout=5, creationflags=0x08000000).stdout  # CREATE_NO_WINDOW
        return "claude.exe" in out.lower()
    except Exception:
        return True  # if we can't tell, don't launch anything


def open_claude():
    """Launch the Claude desktop app (Store install first, then the classic installer, then claude.ai)."""
    try:
        subprocess.Popen(["explorer.exe", r"shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude"])
        return
    except Exception as e:
        log("store launch failed", e)
    exe = os.path.join(os.environ.get("LOCALAPPDATA", ""), "AnthropicClaude", "claude.exe")
    try:
        if os.path.exists(exe):
            subprocess.Popen([exe])
        else:
            os.startfile("https://claude.ai")
    except Exception as e:
        log("open_claude failed", e)


def claude_focused():
    """True when the foreground window is the Claude desktop app or a browser tab on claude.ai."""
    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        h = user32.GetForegroundWindow()
        if not h:
            return False
        n = user32.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, b, n + 1)
        title = b.value
        if "clawd-pet" in title:
            return False
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        exe = ""
        hp = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if hp:
            buf = ctypes.create_unicode_buffer(520)
            size = ctypes.c_ulong(520)
            if kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(size)):
                exe = os.path.basename(buf.value).lower()
            kernel32.CloseHandle(hp)
        if exe == "claude.exe":
            return True
        # claude.ai tabs are titled "Claude" or "<chat name> - Claude"
        browsers = ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe")
        return exe in browsers and ("- Claude" in title or title.startswith("Claude"))
    except Exception:
        return False


def no_activate(win):
    """never steal focus, never show in alt-tab"""
    try:
        user32 = ctypes.windll.user32
        win.update_idletasks()
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        style = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)  # NOACTIVATE | TOOLWINDOW
    except Exception as e:
        log("exstyle failed", e)


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "ClawdPet")
            return True
    except Exception:
        return False


def set_startup(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            exe = sys.executable
            if exe.lower().endswith("python.exe"):
                exe = exe[:-10] + "pythonw.exe"
            winreg.SetValueEx(k, "ClawdPet", 0, winreg.REG_SZ, f'"{exe}" "{os.path.abspath(__file__)}"')
        else:
            try:
                winreg.DeleteValue(k, "ClawdPet")
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------- server
def serve(sock, q):
    while True:
        try:
            conn, _ = sock.accept()
            with conn:
                conn.settimeout(1.0)
                data = b""
                while not data.endswith(b"\n") and len(data) < 4096:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    data += chunk
            msg = data.decode("utf-8", "replace").strip()
            if msg:
                cmd, _, detail = msg.partition("|")
                q.put((cmd, detail))
        except Exception as e:
            log("serve error", e)
            time.sleep(0.2)


# ---------------------------------------------------------------- the pet
class Pet:
    def __init__(self, root, q):
        self.root, self.q = root, q
        dpi = root.winfo_fpixels("1i")
        self.S = S = max(3, round(5 * dpi / 96))
        self.CW, self.CH = 32 * S, 34 * S          # canonical canvas (edge along the bottom)
        self.AW, self.AH = 46 * S, 34 * S          # alert canvas
        self.FW = 34 * S                           # free-floating canvas (dragged / thrown)
        self.free = None                           # (orientation, cx, cy) while floating
        self.GX = (self.CW - 14 * S) // 2          # sprite x inside canonical canvas
        self.HIDE, self.HANDS, self.EYES, self.FULL = -3 * S, 0, int(4.4 * S), 10 * S
        self.font = tkfont.Font(family="Consolas", size=9, weight="bold")

        self.canvas = tk.Canvas(root, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Button-3>", self.on_menu)
        self._geom = None

        # Invisible (1% alpha) window laid over Clawd so the whole body - gaps between legs,
        # the space under his feet - catches the mouse instead of clicking through to apps.
        self.hit = tk.Toplevel(root)
        self.hit.overrideredirect(True)
        self.hit.title("clawd-pet")
        self.hit.config(bg="black", cursor="hand2")
        self.hit.attributes("-topmost", True)
        self.hit.attributes("-alpha", 0.01)
        no_activate(self.hit)
        self._hitgeom = None
        self.set_hit(None)
        self.hit.bind("<ButtonPress-1>", self.on_press)
        self.hit.bind("<B1-Motion>", self.on_motion)
        self.hit.bind("<ButtonRelease-1>", self.on_release)
        self.hit.bind("<Button-3>", self.on_menu)
        self.press = None
        self.drag_hist = collections.deque()
        self.fx = self.fy = self.vx = self.vy = 0.0
        self.swing = 0.0
        self.slide = 0.0  # leftover throw momentum along the edge after landing
        self.grav = "bottom"

        self.menu = tk.Menu(root, tearoff=0)
        self.startup_var = tk.BooleanVar(value=startup_enabled())
        self.menu.add_command(label="Pet Clawd", command=lambda: self.q.put(("hi", "")))
        self.menu.add_command(label="Test permission alert", command=lambda: self.q.put(("alert", "Bash")))
        self.menu.add_command(label="Hide for 10 minutes", command=self.go_away)
        self.menu.add_checkbutton(label="Start with Windows", variable=self.startup_var, command=self.toggle_startup)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=root.destroy)

        now = time.time()
        self.mode = "idle"
        self.edge = "bottom"
        self.pos = self.edge_len() - self.CW * 0.6   # start in the bottom-right corner
        self.target = self.pos
        self.p, self.pgoal = float(self.HIDE), float(self.HANDS)
        self.jump, self.jv = 0.0, 0.0
        self.action, self.action_until = None, 0
        self.next_action = now + 2.5
        self.pending_edge = None
        self.look, self.next_look = 0, now
        self.blink_until, self.next_blink = 0, now + 2
        self.sleeping = False
        self.wave = False
        self.walking = False
        self.bubble = None  # (lines, until)
        self.particles = []
        self.hover, self.last_hover, self.next_heart = False, 0, 0
        self.away_until = 0
        self.after_alert = None
        self.pending_alert = None  # notification waiting to be delivered
        self.last = now
        self.t = 0.0
        self.tick()

    # ---------- geometry helpers
    def edge_len(self):
        l, t, r, b = work_area()
        return (r - l) if self.edge in ("top", "bottom") else (b - t)

    def canvas_size(self):
        return (self.CW, self.CH) if self.edge in ("top", "bottom") else (self.CH, self.CW)

    def window_xy(self):
        l, t, r, b = work_area()
        half = self.CW // 2
        if self.edge == "bottom":
            return l + int(self.pos) - half, b - self.CH
        if self.edge == "top":
            return l + int(self.pos) - half, t
        if self.edge == "left":
            return l, t + int(self.pos) - half
        return r - self.CH, t + int(self.pos) - half

    def place(self, w, h, x, y):
        g = f"{w}x{h}+{x}+{y}"
        if g != self._geom:
            self._geom = g
            self.canvas.config(width=w, height=h)
            self.root.geometry(g)

    def set_hit(self, box):
        """box = screen rect (x0, y0, x1, y1), or None to park the hitbox off-screen"""
        if box is None:
            g = "1x1+-300+-300"
        else:
            x0, y0, x1, y1 = (int(v) for v in box)
            g = f"{max(1, x1 - x0)}x{max(1, y1 - y0)}+{x0}+{y0}"
        if g != self._hitgeom:
            self._hitgeom = g
            self.hit.geometry(g)

    def T(self, x, y, w, h):
        """canonical rect -> actual canvas rect for the current edge"""
        if self.free:  # sprite-local rect, rotated so the feet point at `o`, centred on (cx, cy)
            o, cx, cy = self.free
            lx, ly = x - 7 * self.S, y - 5 * self.S
            if o == "bottom":
                return cx + lx, cy + ly, w, h
            if o == "top":
                return cx - lx - w, cy - ly - h, w, h
            if o == "left":
                return cx - ly - h, cy + lx, h, w
            return cx + ly, cy - lx - w, h, w
        CW, CH = self.CW, self.CH
        e = self.edge
        if e == "bottom":
            return x, y, w, h
        if e == "top":
            return CW - x - w, CH - y - h, w, h
        if e == "left":
            return CH - y - h, x, h, w
        return y, CW - x - w, h, w

    def Tp(self, x, y):
        CW, CH = self.CW, self.CH
        e = self.edge
        if e == "bottom":
            return x, y
        if e == "top":
            return CW - x, CH - y
        if e == "left":
            return CH - y, x
        return y, CW - x

    # ---------- drawing primitives
    def rect(self, x, y, w, h, col, canon=True):
        if canon:
            x, y, w, h = self.T(x, y, w, h)
        self.canvas.create_rectangle(int(x), int(y), int(x + w), int(y + h), fill=C.get(col, col), width=0)

    def pattern(self, cx, cy, pat, col, ps):
        h, w = len(pat), len(pat[0])
        x0, y0 = cx - w * ps / 2, cy - h * ps / 2
        for j, row in enumerate(pat):
            for i, ch in enumerate(row):
                if ch == "X":
                    self.rect(x0 + i * ps, y0 + j * ps, ps, ps, col, canon=False)

    def bubble_at(self, lines, ax, ay, side, cw, ch):
        S = self.S
        tw = max(self.font.measure(s) for s in lines)
        lh = self.font.metrics("linespace")
        pad = int(S * 1.2)
        bw, bh = tw + 2 * pad, lh * len(lines) + 2 * pad
        if side == "up":
            x, y = ax - bw / 2, ay - bh
        elif side == "down":
            x, y = ax - bw / 2, ay
        elif side == "right":
            x, y = ax, ay - bh / 2
        else:
            x, y = ax - bw, ay - bh / 2
        x = max(1, min(cw - bw - 1, x))
        y = max(1, min(ch - bh - 1, y))
        b = max(2, S // 2)
        self.rect(x + b, y, bw - 2 * b, bh, "T", canon=False)
        self.rect(x, y + b, bw, bh - 2 * b, "T", canon=False)
        self.rect(x + b, y + b, bw - 2 * b, bh - 2 * b, "W", canon=False)
        for i, s in enumerate(lines):
            self.canvas.create_text(x + bw / 2, y + pad + lh * i + lh / 2, text=s, font=self.font, fill=C["T"])

    def draw_body(self, ox, top, eyes, look, arms, legs_frame, blush, legs=True):
        """Draw the 14x10 critter with its top-left at canonical (ox, top). arms: 'normal'|'wave'|('grip', y)"""
        S = self.S
        for j, row in enumerate(BODY):
            i = 0
            while i < len(row):
                k = i
                while k < len(row) and row[k] == row[i]:
                    k += 1
                self.rect(ox + (2 + i) * S, top + j * S, (k - i) * S, S, row[i])
                i = k
        # eyes (cols 4 and 9, rows 2-3)
        for c in (4 + look, 9 + look):
            ex = ox + c * S
            if eyes == "open":
                self.rect(ex, top + 2 * S, S, 2 * S, "K")
            elif eyes == "blink":
                self.rect(ex, top + 3 * S, S, S, "K")
            elif eyes == "happy":
                self.rect(ex - S, top + 3 * S, S, S, "K")
                self.rect(ex, top + 2 * S, S, S, "K")
                self.rect(ex + S, top + 3 * S, S, S, "K")
            elif eyes == "sleep":
                self.rect(ex - S, top + 3 * S, 3 * S, S // 2 + 1, "K")
            elif eyes == "squirm":  # > <
                d = -1 if c == 4 + look else 1
                self.rect(ex + d * S, top + 1 * S, S, S, "K")
                self.rect(ex, top + 2 * S, S, S, "K")
                self.rect(ex + d * S, top + 3 * S, S, S, "K")
        if blush:
            self.rect(ox + 3 * S, top + 4 * S, S, S, "P")
            self.rect(ox + 10 * S, top + 4 * S, S, S, "P")
        # legs
        if legs:
            for idx, c in enumerate((3, 5, 8, 10)):
                long_leg = (idx % 2 == 0) if legs_frame == 0 else (idx % 2 == 1)
                hgt = 2 * S if (legs_frame < 0 or long_leg) else S
                self.rect(ox + c * S, top + 8 * S, S, hgt, "D")
        # arms / hands
        if isinstance(arms, tuple):  # gripping the edge
            hy = arms[1]
            for c in (0, 12):
                self.rect(ox + c * S, hy, 2 * S, S, "L")
                self.rect(ox + c * S, hy + S, 2 * S, S, "O")
                self.rect(ox + c * S + S // 2, hy + S, max(1, S // 3), S, "D")
                self.rect(ox + c * S + S + S // 2, hy + S, max(1, S // 3), S, "D")
        elif arms == "flail":
            up = int(self.t * 12) % 2
            self.rect(ox, top + (1 if up else 4) * S, 2 * S, (3 if up else 2) * S, "O")
            self.rect(ox + 12 * S, top + (4 if up else 1) * S, 2 * S, (2 if up else 3) * S, "O")
        else:
            self.rect(ox, top + 4 * S, 2 * S, S, "O")
            self.rect(ox, top + 5 * S, 2 * S, S, "D")
            if arms == "wave" and int(self.t * 5) % 2 == 0:
                self.rect(ox + 12 * S, top + 1 * S, 2 * S, 3 * S, "O")
                self.rect(ox + 12 * S, top + 1 * S, S, S, "L")
            else:
                self.rect(ox + 12 * S, top + 4 * S, 2 * S, S, "O")
                self.rect(ox + 12 * S, top + 5 * S, 2 * S, S, "D")

    # ---------- interaction
    def on_click(self, _e):
        if self.mode == "alert":
            focus_claude()
            self.leave_alert("on it!")
            return
        if self.pending_alert is not None and _e is not None:
            focus_claude()
            self.pending_alert = None
            self.say("on it!", 1.2)
            return
        self.jv = 9.0 * self.S
        self.pgoal = self.FULL
        self.say(random.choice(LINES_CLICK), 1.8)
        for _ in range(4):
            self.spawn("heart")
        self.last_hover = time.time()

    def on_press(self, e):
        self.press = (e.x_root, e.y_root)

    def on_motion(self, e):
        if self.press and self.mode == "idle":
            if abs(e.x_root - self.press[0]) + abs(e.y_root - self.press[1]) > 6:
                self.start_drag()

    def on_release(self, e):
        was_press = self.press is not None
        self.press = None
        if self.mode == "drag":
            self.throw()
        elif was_press:
            self.on_click(e)

    def start_drag(self):
        S = self.S
        # start from where he is drawn so there's no jump
        box = self.sprite_screen_box()
        if box:
            self.fx, self.fy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        else:
            px, py = self.root.winfo_pointerxy()
            self.fx, self.fy = px, py + 4 * S
        self.swing = 0.0
        self.slide = 0.0
        self.mode = "drag"
        self.drag_hist.clear()
        self.particles = []
        self.hover = False
        self.action = None
        self.pending_edge = None
        self.sleeping = False
        self.say(random.choice(LINES_GRAB), 1.6)

    def throw(self):
        now = time.time()
        px, py = self.root.winfo_pointerxy()
        self.drag_hist.append((now, px, py))  # include the exact release point
        vx, vy = self.drag_velocity(0.09)
        if vx == 0.0 and vy == 0.0:
            vx, vy = self.drag_velocity(0.2)
        cap = 5000 * self.S / 5
        sp = math.hypot(vx, vy)
        if sp > cap:
            vx, vy = vx * cap / sp, vy * cap / sp
        self.vx, self.vy = vx, vy
        # gravity pulls toward the nearest edge; in the middle of the screen he just falls down
        l, t, r, b = work_area()
        nx, ny = (self.fx - l) / (r - l), (self.fy - t) / (b - t)
        if 0.25 < nx < 0.75 and 0.25 < ny < 0.75:
            self.grav = "bottom"
        else:
            d = {"left": self.fx - l, "right": r - self.fx, "top": self.fy - t, "bottom": b - self.fy}
            self.grav = min(d, key=d.get)
        self.mode = "fall"
        self.particles = []
        self.set_hit(None)

    def drag_velocity(self, window):
        """average pointer velocity over the last `window` seconds"""
        now = time.time()
        hist = [h for h in self.drag_hist if now - h[0] <= window]
        if len(hist) >= 2 and hist[-1][0] - hist[0][0] > 0.012:
            dt = hist[-1][0] - hist[0][0]
            return (hist[-1][1] - hist[0][1]) / dt, (hist[-1][2] - hist[0][2]) / dt
        return 0.0, 0.0

    def land(self, edge):
        now = time.time()
        l, t, r, b = work_area()
        self.mode = "idle"
        self.edge = edge
        L = self.edge_len()
        along = (self.fx - l) if edge in ("top", "bottom") else (self.fy - t)
        m = self.CW * 0.55
        self.pos = max(m, min(L - m, along))
        self.target = self.pos
        self.p = float(self.FULL)          # touches down exactly where he was flying...
        self.pgoal = float(self.HANDS)     # ...and immediately slips down into hiding
        self.jump, self.jv = 0.0, 0.0
        along_v = self.vx if edge in ("top", "bottom") else self.vy
        cap = 2500 * self.S / 5
        self.slide = max(-cap, min(cap, along_v * 0.6))
        self.action, self.action_until = "landed", now + 0.45
        self.next_action = now + random.uniform(4, 7)
        self.pending_edge = None
        self.hover = False
        self.bubble = None
        self.particles = []
        self._geom = None
        for _ in range(2):
            self.spawn("spark")

    def tick_drag(self, now, dt):
        S = self.S
        px, py = self.root.winfo_pointerxy()
        self.drag_hist.append((now, px, py))
        while self.drag_hist and now - self.drag_hist[0][0] > 0.2:
            self.drag_hist.popleft()
        # held by the top of his head; ease toward the cursor so he glides instead of snapping
        k = 1 - math.exp(-dt * 28)
        self.fx += (px - self.fx) * k
        self.fy += (py + 4 * S - self.fy) * k
        # body swings behind your hand when you move him sideways
        vx, _ = self.drag_velocity(0.06)
        target = max(-3.0 * S, min(3.0 * S, -vx * S / 450))
        self.swing += (target - self.swing) * min(1.0, dt * 10)
        F = self.FW
        self.place(F, F, int(self.fx - F / 2), int(self.fy - F / 2))
        self.canvas.delete("all")
        wig = math.sin(self.t * 15) * S * 0.45
        self.free = ("bottom", F / 2 + self.swing + wig, F / 2)
        self.draw_body(0, 0, "squirm", 0, "flail", int(self.t * 16) % 2, blush=True)
        self.free = None
        if random.random() < 0.12:
            side = random.choice((-1, 1))
            self.particles.append({"x": F / 2 + side * 6 * S, "y": F / 2 - 4 * S, "vx": side * 6 * S,
                                   "vy": -4 * S, "life": 0.6, "kind": "sweat", "abs": True})
        if self.bubble and now < self.bubble[1]:
            self.bubble_at(self.bubble[0], F / 2, F / 2 - 6 * S, "up", F, F)
        self.draw_particles(dt)

    def tick_fall(self, now, dt):
        S = self.S
        l, t, r, b = work_area()
        gx, gy = {"bottom": (0, 1), "top": (0, -1), "left": (-1, 0), "right": (1, 0)}[self.grav]
        G = 3300 * S / 5
        self.vx += gx * G * dt
        self.vy += gy * G * dt
        drag = max(0.0, 1 - 0.35 * dt)
        self.vx *= drag
        self.vy *= drag
        self.fx += self.vx * dt
        self.fy += self.vy * dt
        reach = 5 * S
        hits = []
        if self.fx - l <= reach and self.vx < 0:
            hits.append(("left", self.fx - l))
        if r - self.fx <= reach and self.vx > 0:
            hits.append(("right", r - self.fx))
        if self.fy - t <= reach and self.vy < 0:
            hits.append(("top", self.fy - t))
        if b - self.fy <= reach and self.vy > 0:
            hits.append(("bottom", b - self.fy))
        if hits:
            self.land(min(hits, key=lambda h: h[1])[0])
            return
        orient = self.grav
        soon = []
        if self.vx < 0:
            soon.append(("left", (self.fx - l - reach) / -self.vx))
        if self.vx > 0:
            soon.append(("right", (r - self.fx - reach) / self.vx))
        if self.vy < 0:
            soon.append(("top", (self.fy - t - reach) / -self.vy))
        if self.vy > 0:
            soon.append(("bottom", (b - self.fy - reach) / self.vy))
        if soon:
            edge, eta = min(soon, key=lambda e: e[1])
            if eta < 0.1:
                orient = edge
        F = self.FW
        self.place(F, F, int(self.fx - F / 2), int(self.fy - F / 2))
        self.canvas.delete("all")
        self.free = (orient, F / 2, F / 2)
        self.draw_body(0, 0, "open", 0, "flail", int(self.t * 14) % 2, blush=False)
        self.free = None
        self.draw_particles(dt)

    def on_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def toggle_startup(self):
        try:
            set_startup(self.startup_var.get())
        except Exception as ex:
            log("startup toggle failed", ex)
        self.startup_var.set(startup_enabled())

    def go_away(self):
        self.mode = "away"
        self.away_until = time.time() + 600
        self.root.withdraw()

    def say(self, text, secs):
        self.bubble = (text.split("\n"), time.time() + secs)

    def spawn(self, kind, absolute=None):
        S = self.S
        if absolute:
            x, y = absolute
        else:
            x = self.GX + 7 * S + random.uniform(-5, 5) * S
            y = self.CH - self.p - self.jump + random.uniform(0, 3) * S
        self.particles.append({
            "x": x, "y": y, "vx": random.uniform(-1, 1) * S * 2, "vy": -random.uniform(5, 9) * S,
            "life": 1.0, "kind": kind, "abs": absolute is not None,
        })

    # ---------- messages
    def handle_messages(self):
        while True:
            try:
                cmd, detail = self.q.get_nowait()
            except queue.Empty:
                return
            log("msg", cmd, detail)
            if cmd == "alert":
                if claude_focused():  # you're already looking at Claude
                    continue
                if self.mode == "alert":
                    self.alert_detail = (detail or "").strip()[:22] or self.alert_detail
                    self.alert_leaving = False
                else:
                    if self.mode == "away":
                        self.come_back()
                    # idle: hide first, then drop in at the top (or tell you in place if you're petting it)
                    self.pending_alert = detail or ""
                    self.pending_t0 = time.time()
                    self.pending_edge = None
                    self.action = None
                    self.sleeping = False
                    self.wave = False
                    self.target = self.pos
            elif cmd == "clear":
                if self.pending_alert is not None and time.time() - self.pending_t0 > 1.5:
                    self.pending_alert = None
                if self.mode == "alert" and time.time() - self.alert_t0 > 1.5:
                    self.leave_alert("thanks!")
            elif cmd == "done":
                if self.mode == "alert":
                    self.after_alert = "done"
                    self.leave_alert("thanks!")
                elif not claude_focused():
                    self.cheer()
            elif cmd == "hi":
                if self.mode == "away":
                    self.come_back()
                if self.mode == "idle":
                    self.on_click(None)
            elif cmd == "quit":
                self.root.destroy()

    def come_back(self):
        self.mode = "idle"
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self._geom = None

    def cheer(self):
        if self.mode in ("away", "drag", "fall"):
            return
        now = time.time()
        self.action, self.action_until = "cheer", now + 2.8
        self.pending_edge = None
        self.target = self.pos
        self.pgoal = self.FULL
        self.sleeping = False
        self.jv = 10.0 * self.S
        self.say(random.choice(LINES_DONE), 2.6)
        for _ in range(6):
            self.spawn("spark")
        self.next_action = self.action_until + random.uniform(2, 4)

    # ---------- alert mode
    def start_alert(self, detail):
        if self.mode == "away":
            self.come_back()
        now = time.time()
        self.alert_t0 = now
        self.alert_detail = (detail or "").strip()[:22]
        if self.mode != "alert":
            self.mode = "alert"
            self.alert_drop, self.alert_v = -16.0 * self.S, 0.0
            self.alert_leaving = False
            self.particles = []
            self.bubble = None
        else:
            self.alert_leaving = False

    def leave_alert(self, text):
        if self.mode != "alert" or self.alert_leaving:
            return
        self.alert_leaving = True
        self.alert_bye = text
        self.alert_leave_t = time.time()

    def tick_alert(self, now, dt):
        S = self.S
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        self.place(self.AW, self.AH, sw // 2 - self.AW // 2, 0)
        if not self.alert_leaving and now > getattr(self, "next_focus_check", 0):
            self.next_focus_check = now + 0.25
            if claude_focused():  # you switched to Claude - job done
                self.after_alert = None
                self.leave_alert("go go!")
        # spring drop / climb
        goal = 0.0
        if self.alert_leaving and now - self.alert_leave_t > 0.9:
            goal = -18.0 * S
        self.alert_v += ((goal - self.alert_drop) * 40 - self.alert_v * 7) * dt
        self.alert_drop += self.alert_v * dt
        if self.alert_leaving and self.alert_drop < -15 * S and now - self.alert_leave_t > 1.0:
            self.mode = "idle"
            self.edge = "top"
            l, t, r, b = work_area()
            self.pos = sw // 2 - l
            self.target = self.pos
            self.p, self.pgoal = float(self.HIDE), float(self.EYES)
            self.next_action = now + 3
            self.action = None
            self.particles = []
            self._geom = None
            if self.after_alert == "done":
                self.after_alert = None
                self.cheer()
            return

        c = self.canvas
        c.delete("all")
        swing = math.sin(self.t * 2.4) * S * 1.3 if not self.alert_leaving else 0
        ox = self.AW / 2 - 7 * S + swing
        top = self.alert_drop
        # hover check on the dangling body
        px, py = self.root.winfo_pointerxy()
        wx, wy = self.root.winfo_rootx(), self.root.winfo_rooty()
        hov = ox - S <= px - wx <= ox + 15 * S and top <= py - wy <= top + 12 * S
        self.set_hit((wx + ox - S, wy, wx + ox + 16 * S, wy + max(S, top + 12 * S)))

        # arms reaching up, hands holding the top edge
        for cx in (1, 11):
            self.rect(ox + cx * S, top, 2 * S, S, "L", canon=False)
            self.rect(ox + (cx + (1 if cx == 1 else 0)) * S, top + S, S, S, "O", canon=False)
        # body rows 2..9
        for j, row in enumerate(BODY):
            for i, ch in enumerate(row):
                self.rect(ox + (2 + i) * S, top + (2 + j) * S, S, S, ch, canon=False)
        # big worried eyes
        blink = (self.t % 3.1) < 0.12
        for ex in (4, 8):
            if blink and not self.alert_leaving:
                self.rect(ox + ex * S, top + 5 * S, 2 * S, S // 2 + 1, "K", canon=False)
            elif self.alert_leaving or hov:
                self.rect(ox + ex * S, top + 5 * S, S, S, "K", canon=False)
                self.rect(ox + (ex + 1) * S, top + 4 * S, S, S, "K", canon=False)
            else:
                self.rect(ox + ex * S, top + 4 * S, 2 * S, 2 * S, "K", canon=False)
                self.rect(ox + ex * S, top + 4 * S, S // 2 + 1, S // 2 + 1, "W", canon=False)
        if hov or self.alert_leaving:
            self.rect(ox + 3 * S, top + 6 * S, S, S, "P", canon=False)
            self.rect(ox + 10 * S, top + 6 * S, S, S, "P", canon=False)
        # kicking legs
        kick = int(self.t * 6) % 2
        for idx, cx in enumerate((3, 5, 8, 10)):
            hgt = 2 * S if (idx % 2) == kick else S
            self.rect(ox + cx * S, top + 10 * S, S, hgt, "D", canon=False)
        # flashing "!"
        if not self.alert_leaving and int(self.t * 3) % 2 == 0:
            ex = ox + 15 * S
            self.rect(ex, top + 2 * S, S, 3 * S, "R", canon=False)
            self.rect(ex, top + 6 * S, S, S, "R", canon=False)
        # speech
        if self.alert_leaving:
            lines = [self.alert_bye]
        elif hov:
            lines = ["click me to", "open Claude"]
        else:
            lines = ["psst! Claude needs", "your permission"]
            if self.alert_detail:
                lines.append("-> " + self.alert_detail)
        self.bubble_at(lines, self.AW / 2, top + 13 * S, "down", self.AW, self.AH)
        if self.alert_leaving and random.random() < 0.3:
            self.spawn("heart", absolute=(ox + 7 * S, top + 6 * S))
        self.draw_particles(dt)

    # ---------- idle mode
    def random_pos(self):
        L = self.edge_len()
        m = self.CW * 0.55
        if random.random() < 0.6:  # corners are cosy
            base = m if random.random() < 0.5 else L - m
            return max(m, min(L - m, base + random.uniform(0, 1) * 18 * self.S * (1 if base == m else -1)))
        return random.uniform(m, L - m)

    def choose_action(self, now):
        S = self.S
        r = random.random()
        self.wave = False
        self.sleeping = False
        if r < 0.36:
            self.action = "wander"
            self.target = self.random_pos()
            self.pgoal = random.choice([self.HANDS, self.HANDS, self.EYES])
            self.action_until = now + random.uniform(4, 8)
        elif r < 0.58:
            self.action = "peek"
            self.pgoal = self.EYES
            self.action_until = now + random.uniform(2.5, 4.5)
        elif r < 0.72:
            self.action = "popup"
            self.pgoal = self.FULL
            self.wave = True
            self.action_until = now + random.uniform(2.2, 3.2)
            if random.random() < 0.5:
                self.say(random.choice(LINES_POP), 2.0)
        elif r < 0.86:
            self.action = "switch"
            self.pending_edge = random.choice([e for e in ("bottom", "top", "left", "right") if e != self.edge])
            self.pgoal = self.HIDE
            self.action_until = now + 3
        else:
            self.action = "sleep"
            self.sleeping = True
            self.pgoal = int(3.2 * S)
            self.action_until = now + random.uniform(8, 14)
        self.next_action = self.action_until + random.uniform(1.5, 4.5)

    def sprite_screen_box(self):
        S = self.S
        top = self.CH - self.p - self.jump
        hand_y = min(top + 4 * S, self.CH - 2 * S - min(0, self.p))
        if self.p <= self.HIDE + S:
            return None
        y0 = min(top, hand_y) - S // 2
        y1 = self.CH  # all the way down to the screen edge, even mid-hop
        if y1 <= y0:
            return None
        x, y, w, h = self.T(self.GX - S, y0, 16 * S, y1 - y0)
        wx, wy = self.window_xy()
        return wx + x, wy + y, wx + x + w, wy + y + h

    def tick_idle(self, now, dt):
        S = self.S
        # hover
        box = self.sprite_screen_box()
        self.set_hit(box)
        px, py = self.root.winfo_pointerxy()
        m = 2 * S if self.hover else 0  # a little stickier once you're petting him
        inside = box is not None and box[0] - m <= px <= box[2] + m and box[1] - m <= py <= box[3] + m
        if inside:
            if not self.hover:
                self.hover = True
                self.target = self.pos
                self.pending_edge = None
                self.sleeping = False
                self.wave = False
                self.action = "petted"
                if random.random() < 0.4:
                    self.say(random.choice(["hehe", "<3", "purr", "hi!"]), 1.5)
            self.last_hover = now
            self.pgoal = self.FULL
            if now > self.next_heart:
                self.spawn("heart")
                self.next_heart = now + 0.35
            if self.jump == 0 and random.random() < 0.025:
                self.jv = 5.0 * S
        elif self.hover and now - self.last_hover > 0.08:
            self.hover = False
            self.action = None
            if self.pending_alert is None:
                self.bubble = None
            self.pgoal = self.EYES
            self.sink_fast_until = now + 0.6
            self.next_action = now + random.uniform(1.5, 3)

        if self.pending_alert is not None:
            if now > getattr(self, "next_focus_check", 0):
                self.next_focus_check = now + 0.25
                if claude_focused():
                    self.pending_alert = None
        if self.pending_alert is not None:
            pa = self.pending_alert
            if self.hover:
                # you're playing with it: tell you right here
                lines = ["psst! Claude needs", "your permission"] + (["-> " + pa] if pa else []) + ["(click me)"]
                self.bubble = (lines, now + 0.5)
            else:
                # duck out of sight, then pop up at the top of the screen
                self.pgoal = self.HIDE
                self.sink_fast_until = now + 0.3
                self.target = self.pos
                if self.p <= self.HIDE + 1:
                    self.pending_alert = None
                    self.start_alert(pa)
                    return
        elif not self.hover:
            if self.action in ("peek", "popup", "sleep", "cheer", "landed") and now > self.action_until:
                landed = self.action == "landed"
                self.action = None
                self.wave = False
                self.sleeping = False
                self.pgoal = self.HANDS if landed else random.choice([self.HANDS, self.EYES])
            if now >= self.next_action and self.pending_edge is None:
                self.choose_action(now)

        # edge hop once fully hidden
        if self.pending_edge and self.p <= self.HIDE + 1:
            self.edge = self.pending_edge
            self.pending_edge = None
            self.pos = self.random_pos()
            self.target = self.pos
            self.pgoal = self.EYES if random.random() < 0.5 else self.HANDS
            self._geom = None

        # sliding along the edge after a throw
        if abs(self.slide) > 2 and not self.hover:
            L, m = self.edge_len(), self.CW * 0.55
            self.pos += self.slide * dt
            self.slide *= math.exp(-dt * 4.5)
            if self.pos < m or self.pos > L - m:
                self.pos = max(m, min(L - m, self.pos))
                self.slide = 0.0
            self.target = self.pos
            self.look = 1 if self.slide > 0 else -1
        else:
            self.slide = 0.0

        # walking along the edge (only while low)
        diff = self.target - self.pos
        if abs(diff) > 1 and self.p < self.FULL * 0.7:
            step = min(abs(diff), 70 * S / 5 * dt)
            self.pos += step if diff > 0 else -step
            self.walking = True
            self.look = 1 if diff > 0 else -1
        else:
            self.walking = False
            if now > self.next_look:
                self.look = random.choice([-1, 0, 0, 1]) if self.action in ("peek", "popup", None) else 0
                self.next_look = now + random.uniform(0.7, 2.0)

        # ease peek height, jump physics
        if self.pgoal > self.p:
            speed = 12
        else:
            speed = 16 if now < getattr(self, "sink_fast_until", 0) else 6
            if self.jump > 0 and speed == 16:
                self.jump, self.jv = 0.0, 0.0
        self.p += (self.pgoal - self.p) * min(1.0, dt * speed)
        self.jv -= 40 * S * dt
        self.jump += self.jv * dt * 3
        if self.jump <= 0:
            self.jump, self.jv = 0.0, 0.0

        # blinking
        if now > self.next_blink:
            self.blink_until = now + 0.13
            self.next_blink = now + random.uniform(2, 5)

        if self.sleeping and random.random() < 0.03:
            self.spawn("z")
        if self.action == "cheer" and random.random() < 0.15:
            self.spawn("spark")

        # render
        w, h = self.canvas_size()
        x, y = self.window_xy()
        self.place(w, h, x, y)
        self.draw_idle(now, dt)

    def draw_idle(self, now, dt):
        S = self.S
        c = self.canvas
        c.delete("all")
        top = self.CH - self.p - self.jump
        if self.sleeping:
            eyes = "sleep"
        elif self.hover or self.action in ("cheer", "landed"):
            eyes = "happy"
        elif now < self.blink_until:
            eyes = "blink"
        else:
            eyes = "open"
        hand_y = self.CH - 2 * S - min(0, self.p)
        gripping = top + 4 * S > hand_y
        arms = ("grip", hand_y) if gripping else ("wave" if self.wave or self.action == "cheer" else "normal")
        legs_frame = int(self.t * 8) % 2 if (self.walking or self.hover) else -1
        if self.p > self.HIDE + 1 or not gripping:
            self.draw_body(self.GX, top, eyes, self.look if eyes == "open" else 0, arms, legs_frame,
                           blush=self.hover or self.action == "cheer")
        if self.pending_alert is not None and self.hover and int(self.t * 3) % 2 == 0:
            self.rect(self.GX + 15 * S, top + 0 * S, S, 3 * S, "R")
            self.rect(self.GX + 15 * S, top + 4 * S, S, S, "R")
        # bubble
        if self.bubble:
            lines, until = self.bubble
            if now > until:
                self.bubble = None
            elif self.p > self.EYES - S:
                ax, ay = self.Tp(self.GX + 7 * S, top - S)
                side = {"bottom": "up", "top": "down", "left": "right", "right": "left"}[self.edge]
                w, h = self.canvas_size()
                self.bubble_at(lines, ax, ay, side, w, h)
        self.draw_particles(dt)

    def draw_particles(self, dt):
        S = self.S
        alive = []
        for pt in self.particles:
            pt["life"] -= dt * 0.9
            if pt["life"] <= 0:
                continue
            pt["x"] += pt["vx"] * dt
            pt["y"] += pt["vy"] * dt
            pt["vy"] *= 0.97
            alive.append(pt)
            ax, ay = (pt["x"], pt["y"]) if pt["abs"] else self.Tp(pt["x"], pt["y"])
            ps = max(2, S * 3 // 5)
            if pt["kind"] == "heart":
                self.pattern(ax, ay, HEART, "H", ps)
            elif pt["kind"] == "spark":
                self.pattern(ax, ay, SPARK, "S", ps)
            elif pt["kind"] == "sweat":
                self.pattern(ax, ay, DROP, "B", max(2, S // 2))
            else:
                self.pattern(ax, ay, ZED, "Z", max(2, S // 2))
        self.particles = alive[-40:]

    # ---------- main loop
    def tick(self):
        now = time.time()
        dt = min(0.1, now - self.last)
        self.last = now
        self.t += dt
        try:
            self.handle_messages()
            if self.mode == "alert":
                self.tick_alert(now, dt)
            elif self.mode == "drag":
                self.tick_drag(now, dt)
            elif self.mode == "fall":
                self.tick_fall(now, dt)
            elif self.mode == "away":
                self.set_hit(None)
                if now > self.away_until:
                    self.come_back()
            else:
                self.tick_idle(now, dt)
            if int(self.t * 30) % 90 == 0:  # re-assert always-on-top every ~3s
                self.root.attributes("-topmost", True)
                self.hit.attributes("-topmost", True)
                self.hit.lift()
        except Exception as e:
            log("tick error", repr(e))
        self.root.after(15 if self.mode in ("drag", "fall", "alert") else 33, self.tick)


def main():
    set_dpi_aware()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", PORT))
    except OSError:
        return  # already running
    sock.listen(8)
    q = queue.Queue()
    threading.Thread(target=serve, args=(sock, q), daemon=True).start()
    if len(sys.argv) > 1:
        q.put((sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))

    prev_fg = ctypes.windll.user32.GetForegroundWindow()
    root = tk.Tk()
    root.withdraw()
    root.title("clawd-pet")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", KEY)
    root.config(bg=KEY)
    no_activate(root)
    root.deiconify()
    root.update_idletasks()
    if prev_fg:
        ctypes.windll.user32.SetForegroundWindow(prev_fg)
    Pet(root, q)
    log("clawd started")

    def ensure_claude():
        if not claude_running():
            log("Claude not running - opening it")
            open_claude()
    threading.Thread(target=ensure_claude, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
