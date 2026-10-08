"""
Clawd - a tiny pixel Claude critter that lives on the edges of your screen.

- Hides behind screen edges (you can see its little hands holding on),
  shuffles along the edges, peeks, pops up, waves, naps.
- Hover it to pet it (happy eyes + hearts). Click it to make it hop.
- When Claude Code needs your permission it drops down from the top of the
  screen (by the camera) and dangles there until you deal with it.
- When the Claude app opens (or a new project starts) it leaps onto the app's
  input box and perches above the Send button. Drag it off to send it back.
- Drop it in the middle of the screen while Claude is closed and a window grows
  out of it as it opens the app.
- Right-click for a menu.

Controlled over localhost TCP by notify.py (called from Claude Code hooks).
"""
import base64
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

KEY = "#000000"  # transparent colour key: pure black, so any not-yet-painted area is see-through too
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


ctypes.windll.user32.GetForegroundWindow.restype = ctypes.c_void_p


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


def _hwnd_title(h):
    user32 = ctypes.windll.user32
    n = user32.GetWindowTextLengthW(ctypes.c_void_p(h))
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(ctypes.c_void_p(h), b, n + 1)
    return b.value


def _hwnd_exe(h):
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(ctypes.c_void_p(h), ctypes.byref(pid))
    exe = ""
    hp = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if hp:
        buf = ctypes.create_unicode_buffer(520)
        size = ctypes.c_ulong(520)
        if kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(size)):
            exe = os.path.basename(buf.value).lower()
        kernel32.CloseHandle(hp)
    return exe


def find_claude_window():
    """Handle of the Claude desktop app's main window, or None."""
    try:
        user32 = ctypes.windll.user32
        found = []
        proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(h, _):
            if h and user32.IsWindowVisible(ctypes.c_void_p(h)) and _hwnd_title(h) == "Claude" \
                    and _hwnd_exe(h) == "claude.exe":
                found.append(h)
                return False
            return True

        user32.EnumWindows(proto(cb), 0)
        return found[0] if found else None
    except Exception:
        return None


def window_rect(h, visible=False):
    """(l, t, r, b) of a window; visible=True leaves out the invisible resize borders."""
    r = RECT()
    if visible and ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(h), 9, ctypes.byref(r), ctypes.sizeof(r)) == 0:  # DWMWA_EXTENDED_FRAME_BOUNDS
        return r.l, r.t, r.r, r.b
    if ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(h), ctypes.byref(r)):
        return r.l, r.t, r.r, r.b
    return None


def is_iconic(h):
    return bool(ctypes.windll.user32.IsIconic(ctypes.c_void_p(h)))


def foreground_is(h):
    return bool(h) and ctypes.windll.user32.GetForegroundWindow() == h


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


class SendLocator:
    """Asks Windows UI Automation where the Claude app's Send button is, through one long-lived
    PowerShell helper (starting PowerShell is slow, querying it is quick)."""
    SCRIPT = r'''
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type 'using System.Runtime.InteropServices; public class ClawdDpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }'
[ClawdDpi]::SetProcessDPIAware() | Out-Null
$A = [System.Windows.Automation.AutomationElement]
$cond = New-Object System.Windows.Automation.AndCondition -ArgumentList @(,[System.Windows.Automation.Condition[]]@(
  (New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)),
  (New-Object System.Windows.Automation.PropertyCondition($A::NameProperty, 'Send'))))
$condP = New-Object System.Windows.Automation.AndCondition -ArgumentList @(,[System.Windows.Automation.Condition[]]@(
  (New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)),
  (New-Object System.Windows.Automation.PropertyCondition($A::NameProperty, 'Prompt'))))
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($line -eq $null) { break }
  $out = 'none'
  try {
    $root = $A::FromHandle([IntPtr][long]$line)
    $best = $null
    foreach ($e in $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)) {
      $r = $e.Current.BoundingRectangle
      if ($e.Current.IsOffscreen -or $r.Width -le 0 -or $r.Height -le 0) { continue }
      if ($best -eq $null -or $r.Y -gt $best.Y) { $best = $r }
    }
    if ($best -ne $null) { $out = '{0} {1} {2} {3}' -f [int]$best.X, [int]$best.Y, [int]$best.Width, [int]$best.Height }
    else {
      # no Send button right now: estimate where it sits, just right of the prompt box
      $ed = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condP)
      if ($ed -ne $null -and -not $ed.Current.IsOffscreen) {
        $r = $ed.Current.BoundingRectangle
        if ($r.Width -gt 0) { $out = '{0} {1} {2} {3}' -f [int]($r.X + $r.Width + $r.Height * 0.27), [int]($r.Y - $r.Height * 0.1), [int]($r.Height * 1.23), [int]($r.Height * 1.2) }
      }
    }
  } catch { }
  [Console]::Out.WriteLine($out)
  [Console]::Out.Flush()
}
'''

    def __init__(self):
        self.proc = None

    def _start(self):
        enc = base64.b64encode(self.SCRIPT.encode("utf-16-le")).decode("ascii")
        self.proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
            creationflags=0x08000000)  # CREATE_NO_WINDOW

    def locate(self, hwnd):
        """screen rect (x, y, w, h) of the Send button, or None"""
        try:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            self.proc.stdin.write(f"{int(hwnd)}\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline().strip()
        except Exception as e:
            log("locator failed", repr(e))
            self.proc = None
            return None
        if not line or line == "none":
            return None
        try:
            x, y, w, h = (int(v) for v in line.split())
            return x, y, w, h
        except ValueError:
            return None


class ClaudeWatcher(threading.Thread):
    """Keeps an eye on the Claude app window (opened / closed) and, while Clawd wants to perch,
    on where its Send button is."""

    def __init__(self, q):
        super().__init__(daemon=True)
        self.q = q
        self.hwnd = find_claude_window()
        self.send = None    # Send button as (from right edge, from bottom edge, w, h) of the window
        self.want = False   # set by the pet while perching / about to perch
        self.force = False  # ask for a fresh look right away
        self.locator = SendLocator()
        self.misses = 0

    def run(self):
        last, last_size = 0.0, None
        while True:
            try:
                h = find_claude_window()
                if h and not self.hwnd:
                    self.q.put(("claude_opened", ""))
                elif self.hwnd and not h:
                    self.send = None
                    self.q.put(("claude_closed", ""))
                self.hwnd = h
                if h and self.want and not is_iconic(h):
                    r = window_rect(h)
                    size = (r[2] - r[0], r[3] - r[1]) if r else None
                    if self.force or size != last_size or time.time() - last > (4.0 if self.send else 1.0):
                        self.force = False
                        sb = self.locator.locate(h)
                        r = window_rect(h)
                        if sb and r:
                            self.send = (r[2] - sb[0], r[3] - sb[1], sb[2], sb[3])
                            self.misses = 0
                        else:  # the app's UI is sometimes mid-redraw; only give up after a few misses
                            self.misses += 1
                            if self.misses >= 3:
                                self.send = None
                        last, last_size = time.time(), size
            except Exception as e:
                log("watcher error", repr(e))
            time.sleep(0.4)


def ease_out_back(u, k=1.4):
    u -= 1
    return 1 + (k + 1) * u * u * u + k * u * u


def lerp_rect(a, b, e):
    return tuple(a[i] + (b[i] - a[i]) * e for i in range(4))


# ---------------------------------------------------------------- the pet
class Pet:
    def __init__(self, root, q, watcher=None):
        self.root, self.q, self.watcher = root, q, watcher
        dpi = root.winfo_fpixels("1i")
        self.scale = dpi / 96
        self.S = S = max(3, round(5 * dpi / 96))
        self.S_big, self.S_small = S, max(2, round(S * 0.5))  # tiny while resting on Claude's input box
        self.size_target, self.next_size_step = S, 0.0
        # idle, dragged and falling all share one square window size, so grabbing / landing only
        # moves the window and never resizes it (a resize briefly exposes unpainted pixels)
        self.CW, self.CH = 34 * S, 34 * S          # canonical canvas (edge along the bottom)
        self.AW, self.AH = 46 * S, 34 * S          # alert canvas
        self.FW = self.CW                          # free-floating canvas (dragged / thrown)
        self.free = None                           # (orientation, cx, cy) while floating
        self.GX = (self.CW - 14 * S) // 2          # sprite x inside canonical canvas
        self.HIDE, self.HANDS, self.EYES, self.FULL = -3 * S, 0, int(4.4 * S), 10 * S
        self.font = tkfont.Font(family="Consolas", size=9, weight="bold")
        self.panel_font = tkfont.Font(family="Consolas", size=15, weight="bold")

        self.canvas = tk.Canvas(root, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        root.pack_propagate(False)
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
        self.menu.add_command(label="Sit on Claude's input box", command=lambda: self.q.put(("perch", "")))
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
        self.perched = False        # sitting on the Claude app's input box
        self.perch_pt = None        # screen point on top of the input box, above Send
        self.perch_wanted_until = 0
        self.leap = None            # arc from wherever he is onto the input box
        self.sm = None              # "summon Claude" sequence
        self.pan = None             # the window that grows out of him
        self.panel = self.panel_cv = None
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
        if self.perched and self.perch_pt:  # standing on Claude's input box
            return int(self.perch_pt[0] - self.CW / 2), int(self.perch_pt[1] - self.CH)
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
            self.root.geometry(g)  # resize/move the window first, in one step
            self.canvas.config(width=w, height=h)

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
        if self.jump < 1.5 * self.S:  # only hop from the ground, so spam-clicking can't stack hops
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
        self.perched = False
        self.perch_wanted_until = 0
        self.set_size(self.S_big)  # grows back in your hand
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
            # dropped (not flung) in the middle while the Claude app isn't open: open it
            if self.watcher and not self.watcher.hwnd and math.hypot(vx, vy) < 1800 * self.S / 5:
                self.start_summon()
                return
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
        self.perched = False
        self.set_size(self.S_big, now_=True)
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

    # ---------- size (pixel scale)
    def apply_size(self, s):
        """switch pixel scale, keeping his height/jump proportional so nothing jumps"""
        k = s / self.S
        self.S = s
        self.GX = (self.CW - 14 * s) // 2
        self.HIDE, self.EYES, self.FULL = -3 * s, int(4.4 * s), 10 * s
        self.p *= k
        self.pgoal *= k
        self.jump *= k
        self.jv *= k

    def set_size(self, s, now_=False):
        self.size_target = s
        if now_:
            while self.S != s:
                self.apply_size(self.S + (1 if s > self.S else -1))

    def step_size(self, now):
        if self.S != self.size_target and now >= self.next_size_step:
            self.apply_size(self.S + (1 if self.size_target > self.S else -1))
            self.next_size_step = now + 0.07  # one pixel-size step at a time

    # ---------- perching on the Claude app's input box
    def perch_point(self):
        """screen point on the top edge of Claude's input box, above the Send button (or None)"""
        w = self.watcher
        if not w or not w.hwnd or not w.send or is_iconic(w.hwnd):
            return None
        r = window_rect(w.hwnd)
        if not r:
            return None
        dx, dy, bw, bh = w.send
        return r[2] - dx + bw / 2, r[3] - dy - 7.5 * self.scale

    def request_perch(self, secs=25):
        self.perch_wanted_until = time.time() + secs
        if self.watcher:
            self.watcher.want = True
            self.watcher.force = True

    def unperch(self, fall=True):
        S = self.S
        pt = self.perch_pt
        visible = self.mode == "idle" and self.p > self.HIDE + S
        self.perched = False
        self.set_size(self.S_big)
        if fall and visible and pt:  # hop off and drop to the bottom of the screen
            top = self.CH - self.p - self.jump
            self.fx, self.fy = pt[0], pt[1] - self.CH + top + 5 * S
            self.vx, self.vy = 0.0, -3.0 * S
            self.grav = "bottom"
            self.mode = "fall"
            self.set_hit(None)
        else:
            l, t, r, b = work_area()
            m = self.CW * 0.55
            self.edge = "bottom"
            self.pos = self.target = max(m, min(r - l - m, (pt[0] if pt else (l + r) / 2) - l))
            self.p, self.pgoal = float(self.HIDE), float(self.HANDS)
            self._geom = None

    def start_leap(self):
        S = self.S
        pt = self.perch_point()
        if not pt:
            return False
        if self.mode == "idle":
            box = self.sprite_screen_box()
            if box:
                x0, y0 = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            else:  # hidden behind an edge: jump out of it
                wx, wy = self.window_xy()
                ax, ay = self.Tp(self.GX + 7 * S, self.CH)
                x0, y0 = wx + ax, wy + ay
        else:
            x0, y0 = self.fx, self.fy
        self.perch_pt = pt
        tx, ty = pt[0], pt[1] - 5 * S
        dist = math.hypot(tx - x0, ty - y0)
        self.leap = {"x0": x0, "y0": y0, "t0": time.time(), "T": max(0.6, min(1.3, 0.55 + dist / 2600)),
                     "H": max(140 * self.scale, 0.3 * dist)}
        self.fx, self.fy = x0, y0
        self.set_size(self.S_small)  # shrinks step by step during the jump
        self.mode = "leap"
        self.perch_wanted_until = 0
        self.hover = self.sleeping = self.wave = False
        self.action = self.bubble = self.pending_edge = None
        self.slide = 0.0
        self.particles = []
        self.set_hit(None)
        return True

    def tick_leap(self, now, dt):
        S = self.S
        lp = self.leap
        pt = self.perch_point()
        if pt:
            self.perch_pt = pt  # follow the window if it moves mid-jump
        elif not (self.watcher and self.watcher.hwnd):  # Claude closed mid-air
            self.vx = self.vy = 0.0
            self.grav = "bottom"
            self.mode = "fall"
            return
        tx, ty = self.perch_pt[0], self.perch_pt[1] - 5 * S
        u = min(1.0, (now - lp["t0"]) / lp["T"])
        e = u * u * (3 - 2 * u)
        self.fx = lp["x0"] + (tx - lp["x0"]) * e
        self.fy = lp["y0"] + (ty - lp["y0"]) * u - lp["H"] * 4 * u * (1 - u)
        if u >= 1:
            self.arrive_perch(now)
            return
        F = self.FW
        self.place(F, F, int(self.fx - F / 2), int(self.fy - F / 2))
        self.canvas.delete("all")
        self.free = ("bottom", F / 2, F / 2)
        tuck = u > 0.75  # arms down, legs out for the landing
        self.draw_body(0, 0, "happy", 0, "normal" if tuck else "flail", -1 if tuck else int(self.t * 14) % 2,
                       blush=True)
        self.free = None
        self.draw_particles(dt)

    def arrive_perch(self, now):
        self.set_size(self.S_small, now_=True)
        self.perched = True
        self.mode = "idle"
        self.edge = "bottom"
        self.p, self.pgoal = float(self.FULL), float(self.EYES)  # (in small units now)
        self.jump, self.jv = 0.0, 0.0
        self.action, self.action_until = "landed", now + 0.6
        self.next_action = now + random.uniform(3, 6)
        self.hover = False
        self.particles = []
        self._geom = None
        for _ in range(3):
            self.spawn("spark")

    def choose_perched_action(self, now):
        S = self.S
        r = random.random()
        self.wave = self.sleeping = False
        self.target = self.pos
        if r < 0.45:
            self.action, self.pgoal = "peek", self.EYES
            self.action_until = now + random.uniform(3, 6)
        elif r < 0.65:
            self.action, self.pgoal = "popup", self.FULL
            self.wave = random.random() < 0.6
            self.action_until = now + random.uniform(1.8, 3.0)
        elif r < 0.8:
            self.action, self.pgoal = "duck", self.HANDS  # just his hands on the box
            self.action_until = now + random.uniform(2, 4)
        else:
            self.action, self.pgoal = "sleep", int(3.2 * S)
            self.sleeping = True
            self.action_until = now + random.uniform(6, 10)
        self.next_action = self.action_until + random.uniform(1.5, 3.5)

    # ---------- summoning the Claude app: a window grows out of Clawd
    def start_summon(self):
        self.set_size(self.S_big, now_=True)
        self.mode = "summon"
        self.sm = {"phase": "charge", "t0": time.time()}
        self.particles = []
        self.set_hit(None)
        self.say("opening Claude!", 1.6)

    def tick_summon(self, now, dt):
        S = self.S
        sm = self.sm
        l, t, r, b = work_area()
        if sm["phase"] == "charge":  # coast to a stop and wind up
            k = math.exp(-dt * 8)
            self.vx *= k
            self.vy *= k
            self.fx += self.vx * dt
            self.fy += self.vy * dt
            if now - sm["t0"] > 0.35:
                open_claude()
                self.start_panel((self.fx - 7 * S, self.fy - 5 * S, self.fx + 7 * S, self.fy + 5 * S))
                sm.update(phase="ride", t_open=now)
        elif sm["phase"] == "ride":  # stand on top of the growing window
            if self.pan:
                x0, y0, x1, y1 = self.pan["cur"]
                tx, ty = (x0 + x1) / 2, max(t + 6 * S, y0 - 5 * S)
                k = 1 - math.exp(-dt * 16)
                self.fx += (tx - self.fx) * k
                self.fy += (ty - self.fy) * k
                if self.pan["phase"] == "hold" and now - sm["t_open"] > 16:  # it never showed up
                    self.pan_fade()
                    self.say("hmm, Claude\ndidn't open", 2.2)
                    sm.update(phase="wait", t_wait=now - 9)
            if sm["phase"] == "ride" and (self.pan is None or self.pan["phase"] in ("morph", "fade")):
                sm.update(phase="wait", t_wait=now)
        elif sm["phase"] == "wait":  # Claude is open: hop onto its input box as soon as it's there
            if self.perch_point() and self.start_leap():
                return
            if now - sm["t_wait"] > 12:
                self.vx = self.vy = 0.0
                self.grav = "bottom"
                self.mode = "fall"
                return
        F = self.FW
        self.place(F, F, int(self.fx - F / 2), int(self.fy - F / 2))
        self.canvas.delete("all")
        bob = math.sin(self.t * 4) * S * 0.3
        self.free = ("bottom", F / 2, F / 2 + bob)
        self.draw_body(0, 0, "happy", 0, "wave", -1, blush=True)
        self.free = None
        if random.random() < 0.08:
            self.particles.append({"x": F / 2 + random.uniform(-6, 6) * S, "y": F / 2 + random.uniform(-4, 2) * S,
                                   "vx": random.uniform(-1, 1) * S * 2, "vy": -random.uniform(4, 7) * S,
                                   "life": 0.8, "kind": "spark", "abs": True})
        if self.bubble and now < self.bubble[1]:
            self.bubble_at(self.bubble[0], F / 2, F / 2 - 6 * S, "up", F, F)
        self.draw_particles(dt)

    def start_panel(self, rect):
        l, t, r, b = work_area()
        if self.panel is None:
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.title("clawd-pet")
            win.attributes("-topmost", True)
            win.attributes("-transparentcolor", KEY)
            win.config(bg=KEY)
            no_activate(win)
            cv = tk.Canvas(win, bg=KEY, highlightthickness=0, bd=0)
            cv.pack(fill="both", expand=True)
            self.panel, self.panel_cv = win, cv
        W, H = r - l, b - t
        self.panel.geometry(f"{W}x{H}+{l}+{t}")  # one full-screen, mostly see-through window: never resized
        self.panel.attributes("-alpha", 1.0)
        self.panel.deiconify()
        self.root.lift()   # Clawd stays in front of the window he's pulling out
        self.hit.lift()
        tw, th = int(W * 0.7), int(H * 0.76)
        to = (l + (W - tw) // 2, t + (H - th) // 2, l + (W + tw) // 2, t + (H + th) // 2)
        self.pan = {"phase": "grow", "t0": time.time(), "from": rect, "to": to, "cur": rect, "origin": (l, t)}

    def pan_fade(self):
        if self.pan and self.pan["phase"] != "fade":
            self.pan.update(phase="fade", t0=time.time())

    def tick_panel(self, now):
        pn = self.pan
        if pn is None:
            return
        el = now - pn["t0"]
        if pn["phase"] == "grow":
            u = min(1.0, el / 0.6)
            pn["cur"] = lerp_rect(pn["from"], pn["to"], ease_out_back(u))
            if u >= 1:
                pn.update(phase="hold", t0=now)
        elif pn["phase"] == "hold":  # wait for the real Claude window to show up
            pn["cur"] = pn["to"]
            h = self.watcher.hwnd if self.watcher else None
            if h and not is_iconic(h):
                rr = window_rect(h, visible=True)
                if rr and rr[2] - rr[0] > 300 and rr[3] - rr[1] > 200:
                    pn.update(phase="morph", t0=now, **{"from": pn["cur"], "to": rr})
                    self.request_perch(30)
        elif pn["phase"] == "morph":  # line up exactly with the real window...
            u = min(1.0, el / 0.3)
            pn["cur"] = lerp_rect(pn["from"], pn["to"], u * u * (3 - 2 * u))
            if u >= 1:
                pn.update(phase="fade", t0=now)
        elif pn["phase"] == "fade":  # ...then fade away to reveal it
            u = min(1.0, el / 0.35)
            self.panel.attributes("-alpha", max(0.0, 1 - u))
            if u >= 1:
                self.panel.withdraw()
                self.pan = None
                return
        self.draw_panel(pn["cur"])

    def draw_panel(self, rect):
        S, cv = self.S, self.panel_cv
        ox, oy = self.pan["origin"]
        x0, y0, x1, y1 = rect[0] - ox, rect[1] - oy, rect[2] - ox, rect[3] - oy
        cv.delete("all")
        w, h = x1 - x0, y1 - y0
        if w < 4 or h < 4:
            return
        c = min(S, w / 4, h / 4)  # chunky pixel corners
        bw = max(2, S // 2)

        def chunky(a0, b0, a1, b1, col, cc):
            cv.create_rectangle(a0 + cc, b0, a1 - cc, b1, fill=col, width=0)
            cv.create_rectangle(a0, b0 + cc, a1, b1 - cc, fill=col, width=0)

        chunky(x0, y0, x1, y1, C["O"], c)
        chunky(x0 + bw, y0 + bw, x1 - bw, y1 - bw, "#262624", max(0, c - bw))
        if h > 14 * S:  # title bar strip
            cv.create_rectangle(x0 + bw + c, y0 + bw, x1 - bw - c, y0 + 5 * S, fill="#30302E", width=0)
        if w > 44 * S and h > 26 * S and self.pan["phase"] in ("grow", "hold"):
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            cv.create_text(cx, cy - 3 * S, text="opening Claude", font=self.panel_font, fill=C["O"])
            for i in range(3):  # bouncing pixel dots
                dy = max(0.0, math.sin(self.t * 7 - i * 0.8)) * 1.5 * S
                dx = cx + (i - 1) * 3 * S
                cv.create_rectangle(dx - S / 2, cy + 3 * S - dy, dx + S / 2, cy + 4 * S - dy, fill=C["O"], width=0)

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
            elif cmd in ("claude_opened", "session", "perch"):
                # Claude app opened / new project / menu: hop onto the app's input box
                if self.perched and cmd == "session" and self.mode == "idle":
                    self.jv = 7.0 * self.S
                    for _ in range(4):
                        self.spawn("spark")
                elif cmd == "claude_opened" or (self.watcher and self.watcher.hwnd):
                    if self.mode == "away":
                        self.come_back()
                    self.request_perch()
            elif cmd == "claude_closed":
                self.perch_wanted_until = 0
                if self.perched and self.mode == "idle":
                    self.unperch(fall=True)
                elif self.perched:
                    self.perched = False
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
        self.set_size(self.S_big, now_=True)
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
            if self.perched:  # back to his spot on Claude's input box
                self.edge = "bottom"
                self.set_size(self.S_small, now_=True)
            else:
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
        if self.perched:
            return self.choose_perched_action(now)
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
        perch_visible = False
        if self.perched:
            pt = self.perch_point()
            if pt:
                self.perch_pt = pt  # follows the window around
            if not (self.watcher and self.watcher.hwnd):
                self.unperch(fall=True)
                if self.mode != "idle":
                    return
            else:
                # only show himself while you're actually looking at Claude
                perch_visible = pt is not None and foreground_is(self.watcher.hwnd)
        elif (now < self.perch_wanted_until and not self.hover and self.pending_alert is None and self.watcher
              and self.watcher.hwnd and foreground_is(self.watcher.hwnd) and self.perch_point()):
            if self.start_leap():
                return
        # hover
        box = self.sprite_screen_box()
        self.set_hit(box)
        px, py = self.root.winfo_pointerxy()
        m = 2 * S if self.hover else 0  # a little stickier once you're petting him
        inside = box is not None and box[0] - m <= px <= box[2] + m and box[1] - m <= py <= box[3] + m
        inside = inside and (perch_visible or not self.perched)
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
        elif not self.hover and self.perched and not perch_visible:
            # you switched away from Claude: duck down behind the input box
            self.pgoal = self.HIDE
            self.sink_fast_until = now + 0.2
            self.action = None
            self.sleeping = self.wave = False
            self.next_action = now + 1.2
        elif not self.hover:
            if self.perched and self.pgoal == self.HIDE:  # you're back: peek over the box again
                self.pgoal = self.EYES
            if self.action in ("peek", "popup", "sleep", "cheer", "landed", "duck") and now > self.action_until:
                landed = self.action == "landed"
                self.action = None
                self.wave = False
                self.sleeping = False
                if self.perched:
                    self.pgoal = self.EYES
                else:
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
        ceiling = max(0.0, self.CH - self.p - 12 * S)  # never jump out of his own window
        if self.jump > ceiling:
            self.jump, self.jv = ceiling, min(self.jv, 0.0)

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
            if self.watcher:
                self.watcher.want = self.perched or self.mode in ("leap", "summon") or now < self.perch_wanted_until
            self.tick_panel(now)
            self.step_size(now)
            if self.mode == "leap":
                self.tick_leap(now, dt)
            elif self.mode == "summon":
                self.tick_summon(now, dt)
            elif self.mode == "alert":
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
        except tk.TclError as e:
            if "destroyed" in str(e):  # quitting: stop quietly
                return
            log("tick error", repr(e))
        except Exception as e:
            log("tick error", repr(e))
        fast = self.mode in ("drag", "fall", "alert", "leap", "summon") or self.pan is not None
        self.root.after(15 if fast else 33, self.tick)


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
    watcher = ClaudeWatcher(q)
    Pet(root, q, watcher)
    watcher.start()
    log("clawd started")

    def ensure_claude():
        if not claude_running():
            log("Claude not running - opening it")
            open_claude()
    threading.Thread(target=ensure_claude, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
