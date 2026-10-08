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
import ctypes
import math
import os
import queue
import random
import socket
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

LINES_CLICK = ["hi!", "*beep*", "hehe", "pat pat", "I'm helping!", "boop", "need a hand?", "hey :)", "clawd!"]
LINES_POP = ["hi :)", "just checking", "still here!", "*yawn*", "o/", "wheee"]
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
    except Exception as e:
        log("focus_claude failed", e)


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


RUN_KEY =r"Software\Microsoft\Windows\CurrentVersion\Run"


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
        self.GX = (self.CW - 14 * S) // 2          # sprite x inside canonical canvas
        self.HIDE, self.HANDS, self.EYES, self.FULL = -3 * S, 0, int(4.4 * S), 10 * S
        self.font = tkfont.Font(family="Consolas", size=9, weight="bold")

        self.canvas = tk.Canvas(root, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Button-3>", self.on_menu)
        self._geom = None

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

    def T(self, x, y, w, h):
        """canonical rect -> actual canvas rect for the current edge"""
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
        if self.mode == "away":
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
        y0 = min(top, hand_y)
        y1 = min(self.CH, max(top + 10 * S, hand_y + 2 * S))
        if y1 <= y0:
            return None
        x, y, w, h = self.T(self.GX - S, y0, 16 * S, y1 - y0)
        wx, wy = self.window_xy()
        return wx + x, wy + y, wx + x + w, wy + y + h

    def tick_idle(self, now, dt):
        S = self.S
        # hover
        box = self.sprite_screen_box()
        px, py = self.root.winfo_pointerxy()
        inside = box is not None and box[0] - 3 <= px <= box[2] + 3 and box[1] - 3 <= py <= box[3] + 3
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
            if self.action in ("peek", "popup", "sleep", "cheer") and now > self.action_until:
                self.action = None
                self.wave = False
                self.sleeping = False
                self.pgoal = random.choice([self.HANDS, self.EYES])
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
        elif self.hover or self.action == "cheer":
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
            elif self.mode == "away":
                if now > self.away_until:
                    self.come_back()
            else:
                self.tick_idle(now, dt)
            if int(self.t * 30) % 90 == 0:  # re-assert always-on-top every ~3s
                self.root.attributes("-topmost", True)
        except Exception as e:
            log("tick error", repr(e))
        self.root.after(33, self.tick)


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
    root.update_idletasks()
    try:  # never steal focus, never show in alt-tab
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        GWL_EXSTYLE = -20
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | 0x08000000 | 0x00000080)  # NOACTIVATE | TOOLWINDOW
    except Exception as e:
        log("exstyle failed", e)
    root.deiconify()
    root.update_idletasks()
    if prev_fg:
        ctypes.windll.user32.SetForegroundWindow(prev_fg)
    Pet(root, q)
    log("clawd started")
    root.mainloop()


if __name__ == "__main__":
    main()
