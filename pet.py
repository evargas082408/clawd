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
import json
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
STATE = os.path.join(HERE, "state.json")  # remembers where Claude's window lives and how long it takes to open
_state_lock = threading.Lock()

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

# ---- love & gacha
STEAM = [".XX.", "XXXX", ".XX."]
CLOUD = [".XX.X.", "XXXXXX", ".XXXX."]
MOOD_LINES = {
    "smitten": {"pop": ["love you!", "hi bestie <3", "you're the best", "missed you!", "*happy wiggle*"],
                "click": ["<3 <3 <3", "hehe stop it", "best human", "boop back!", "yay!"],
                "pet": ["purrr", "<3", "hehe", "more pls"], "grab": ["wheee!", "hehe!", "up we go!"]},
    "happy": {"pop": LINES_POP, "click": LINES_CLICK, "pet": ["hehe", "<3", "purr", "hi!"], "grab": LINES_GRAB},
    "meh": {"pop": ["hm.", "*yawn*", "still here", "..."], "click": ["hm?", "what", "yeah?", "ok"],
            "pet": ["...ok", "hm", "fine"], "grab": ["hey", "ugh", "where to"]},
    "grumpy": {"pop": ["oh, NOW you notice me?", "hmph.", "cool. cool cool cool.", "I see how it is", "don't mind me..."],
               "click": ["what.", "oh hi. finally.", "hmph", "don't poke me"],
               "refuse": ["hmph.", "nope.", "oh NOW you care?", "I'm busy."],
               "pet": ["...", "hmph"], "grab": ["put me DOWN", "rude!", "excuse me??"]},
    "sulky": {"pop": ["...", "go away", "not talking to you", "*sulks*"], "click": ["...", "leave me alone"],
              "refuse": ["...", "go away", "nope", "*turns away*"], "pet": ["..."], "grab": ["...", "whatever"]},
}
MOOD_LUCK = {"smitten": 1.8, "happy": 1.2, "meh": 1.0, "grumpy": 0.6, "sulky": 0.4}  # rare+ odds multiplier
RARITY = {  # base chance, colour, love it gives
    "common": (0.58, "#8D96A0", 1),
    "uncommon": (0.27, "#3FA65A", 2),
    "rare": (0.10, "#3B7FE6", 4),
    "epic": (0.04, "#9B5CF6", 6),
    "legendary": (0.01, "#E0A21A", 10),
}
LOOT = {"rare": ["bow", "flower", "party hat"], "epic": ["sunglasses", "halo"], "legendary": ["crown", "golden skin"]}
ALL_LOOT = ["bow", "flower", "party hat", "sunglasses", "halo", "crown", "golden skin"]
GEAR = {  # (col, row, w, h, colour) in sprite pixels; rows < 0 sit on top of his head
    "crown": [(4, -3, 1, 1, "#F5C451"), (6, -3, 2, 1, "#F5C451"), (9, -3, 1, 1, "#F5C451"),
              (4, -2, 6, 1, "#F5C451"), (6, -2, 2, 1, "#E5484D"), (4, -1, 6, 1, "#C99A2E")],
    "party hat": [(7, -5, 1, 1, "#FFFFFF"), (7, -4, 1, 1, "#F27BA8"), (6, -3, 3, 1, "#5AA9F2"),
                  (6, -2, 3, 1, "#F27BA8"), (5, -1, 5, 1, "#5AA9F2")],
    "bow": [(9, -2, 1, 2, "#F27BA8"), (10, -1, 1, 1, "#C2507E"), (11, -2, 1, 2, "#F27BA8")],
    "flower": [(4, -3, 1, 1, "#FFFFFF"), (3, -2, 1, 1, "#FFFFFF"), (5, -2, 1, 1, "#FFFFFF"),
               (4, -1, 1, 1, "#FFFFFF"), (4, -2, 1, 1, "#F5C451")],
    "sunglasses": [(3, 2, 3, 2, "#151515"), (8, 2, 3, 2, "#151515"), (6, 2, 2, 1, "#151515"),
                   (3, 2, 1, 1, "#707070"), (8, 2, 1, 1, "#707070")],
    "halo": [(5, -4, 4, 1, "#F7D96B"), (4, -3, 1, 1, "#F7D96B"), (9, -3, 1, 1, "#F7D96B")],
}
SKINS = {"classic": None, "golden": {"O": "#E8B23A", "D": "#B98A1C", "L": "#FBE08A"}}


def log(*a):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a) + "\n")
    except Exception:
        pass


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(**kv):
    with _state_lock:
        st = load_state()
        st.update(kv)
        try:
            tmp = STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f)
            os.replace(tmp, STATE)
        except Exception as e:
            log("save_state failed", repr(e))


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
    """Handle of the Claude desktop app's main window (the one in front, if there are several), or None."""
    try:
        user32 = ctypes.windll.user32
        found = []
        proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(h, _):
            if h and user32.IsWindowVisible(ctypes.c_void_p(h)) and _hwnd_title(h) == "Claude" \
                    and _hwnd_exe(h) == "claude.exe":
                found.append(h)
            return True

        user32.EnumWindows(proto(cb), 0)
        fg = user32.GetForegroundWindow()
        if fg in found:
            return fg
        return found[0] if found else None
    except Exception:
        return None


_fg_cache = [None, False]


def claude_in_front():
    """True while any window of the Claude app (main window, its menus and pop-ups) is in front."""
    fg = ctypes.windll.user32.GetForegroundWindow()
    if fg != _fg_cache[0]:
        _fg_cache[0], _fg_cache[1] = fg, bool(fg) and _hwnd_exe(fg) == "claude.exe"
    return _fg_cache[1]


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


_VP = ctypes.c_void_p


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]


def _setup_gdi():
    u32, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    u32.GetDC.restype, u32.GetDC.argtypes = _VP, [_VP]
    u32.ReleaseDC.argtypes = [_VP, _VP]
    u32.PrintWindow.argtypes = [_VP, _VP, ctypes.c_uint]
    gdi.CreateCompatibleDC.restype, gdi.CreateCompatibleDC.argtypes = _VP, [_VP]
    gdi.CreateCompatibleBitmap.restype = _VP
    gdi.CreateCompatibleBitmap.argtypes = [_VP, ctypes.c_int, ctypes.c_int]
    gdi.SelectObject.restype, gdi.SelectObject.argtypes = _VP, [_VP, _VP]
    gdi.DeleteObject.argtypes = [_VP]
    gdi.DeleteDC.argtypes = [_VP]
    gdi.GetDIBits.argtypes = [_VP, _VP, ctypes.c_uint, ctypes.c_uint, _VP, _VP, ctypes.c_uint]


_setup_gdi()


def capture_window(h):
    """The window's own pixels as the app draws them (BGRA, top-down) - windows on top of it, like
    Clawd himself, don't show up. Returns (buf, w, h, left, top) or None."""
    r = window_rect(h)
    if not r or r[2] <= r[0] or r[3] <= r[1]:
        return None
    W, H = r[2] - r[0], r[3] - r[1]
    u32, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    sdc = u32.GetDC(None)
    mdc = gdi.CreateCompatibleDC(sdc)
    bmp = gdi.CreateCompatibleBitmap(sdc, W, H)
    old = gdi.SelectObject(mdc, bmp)
    try:
        if not u32.PrintWindow(_VP(h), mdc, 2):  # PW_RENDERFULLCONTENT
            return None
        bi = _BIH()
        bi.biSize, bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = ctypes.sizeof(_BIH), W, -H, 1, 32
        buf = (ctypes.c_ubyte * (W * H * 4))()
        gdi.GetDIBits(mdc, bmp, 0, H, buf, ctypes.byref(bi), 0)
        return bytes(buf), W, H, r[0], r[1]
    finally:
        gdi.SelectObject(mdc, old)
        gdi.DeleteObject(bmp)
        gdi.DeleteDC(mdc)
        u32.ReleaseDC(None, sdc)


def window_painted(h):
    """True once the window shows real content (text etc.), not just a blank loading surface."""
    cap = capture_window(h)
    if not cap:
        return False
    buf, W, H, _, _ = cap
    colours, bright, n = set(), 0, 0
    for gy in range(1, 30):
        for gx in range(1, 40):
            i = ((H * gy // 30) * W + (W * gx // 40)) * 4
            b, g, r = buf[i], buf[i + 1], buf[i + 2]
            colours.add((r // 24, g // 24, b // 24))
            bright += (r + g + b) > 360
            n += 1
    # a blank loading surface is one flat colour; real UI has text and accents on it
    return len(colours) >= 3 or 0 < bright < 0.6 * n


def orange_critter(buf, W, H, x0, y0, x1, y1, scale):
    """Is there a solid, critter-sized blob of Claude orange in this part of the image?
    (that's the app's own little Clawd, which shows on a brand-new chat)"""
    x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(W, int(x1)), min(H, int(y1))
    n, bx0, by0, bx1, by1 = 0, 1 << 30, 1 << 30, -1, -1
    step = 2
    for y in range(y0, y1, step):
        row = y * W * 4
        for x in range(x0, x1, step):
            i = row + x * 4
            b, g, r = buf[i], buf[i + 1], buf[i + 2]
            if 170 <= r <= 245 and 85 <= g <= 150 and 50 <= b <= 120 and r - g >= 55 and g >= b - 5:
                n += 1
                bx0, by0, bx1, by1 = min(bx0, x), min(by0, y), max(bx1, x), max(by1, y)
    if not n:
        return False
    px = n * step * step
    bw, bh = bx1 - bx0 + step, by1 - by0 + step
    k = scale / 1.5
    return px >= 100 * k * k and bw >= 8 * scale and bh >= 6 * scale and px / (bw * bh) >= 0.3


def mix(c1, c2, t):
    """blend two #rrggbb colours"""
    t = max(0.0, min(1.0, t))
    a, b = int(c1[1:], 16), int(c2[1:], 16)
    return "#%02x%02x%02x" % tuple(int(((a >> s_) & 255) + (((b >> s_) & 255) - ((a >> s_) & 255)) * t)
                                   for s_ in (16, 8, 0))


def smooth(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


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


class ComposerLocator:
    """Asks Windows UI Automation where the Claude app's message boxes are (one per open tab/pane),
    through one long-lived PowerShell helper. It keeps hold of what it found, so checking 5x a second
    is cheap, and looks again when a box is replaced or every 1.5s (to notice newly opened tabs).
    Answers one 'bx by bw bh sx sy sw sh focused' per box, joined by ';' (s* = Send/Stop button, -1 if none)."""
    SCRIPT = r'''
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type 'using System.Runtime.InteropServices; public class ClawdDpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }'
[ClawdDpi]::SetProcessDPIAware() | Out-Null
$A = [System.Windows.Automation.AutomationElement]
$TS = [System.Windows.Automation.TreeScope]
$W = [System.Windows.Automation.TreeWalker]::RawViewWalker
$editCond = New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)
$btnCond = New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
$cache = @{ hwnd = ''; items = @(); t = 0; ids = ''; gen = 0 }

function Ok($e) {
  if ($e -eq $null) { return $false }
  try { $r = $e.Current.BoundingRectangle; return (-not $e.Current.IsOffscreen) -and $r.Width -gt 0 -and $r.Height -gt 0 } catch { return $false }
}

function Find-Box($edit) {
  # the bordered box around a message field: the surface element, else the first padded ancestor
  $et = $edit.Current.BoundingRectangle.Y
  $padded = $null
  $cur = $edit
  for ($i = 0; $i -lt 8; $i++) {
    $cur = $W.GetParent($cur)
    if ($cur -eq $null) { break }
    if ($cur.Current.ClassName -like '*bg-surface*') { return $cur }
    if ($padded -eq $null -and $cur.Current.BoundingRectangle.Y -le $et - 6) { $padded = $cur }
  }
  if ($padded -ne $null) { return $padded }
  return $edit
}

function Find-All($root) {
  $items = @()
  foreach ($e in $root.FindAll($TS::Descendants, $editCond)) {
    if (($e.Current.Name -eq 'Prompt' -or $e.Current.ClassName -like '*ProseMirror*') -and (Ok $e)) {
      $items += ,@($e, (Find-Box $e))
    }
  }
  return ,$items
}

while ($true) {
  $line = [Console]::In.ReadLine()
  if ($line -eq $null) { break }
  $out = 'none'
  try {
    $now = [Environment]::TickCount
    $stale = ($cache.hwnd -ne $line) -or (($now - $cache.t) -gt 1500) -or ($cache.items.Count -eq 0)
    if (-not $stale) { foreach ($it in $cache.items) { if (-not (Ok $it[1])) { $stale = $true } } }
    if ($stale) {
      $cache.hwnd = $line
      $cache.items = Find-All ($A::FromHandle([IntPtr][long]$line))
      $cache.t = $now
      # count how often the set of boxes actually changes (new tab, switched tab, ...)
      $ids = ($cache.items | ForEach-Object { ($_[1].GetRuntimeId() -join '.') }) -join ','
      if ($ids -ne $cache.ids) { $cache.ids = $ids; $cache.gen++ }
    }
    $parts = @()
    foreach ($it in $cache.items) {
      $edit = $it[0]; $box = $it[1]
      if (-not (Ok $box)) { continue }
      $b = $box.Current.BoundingRectangle
      $s = '-1 -1 -1 -1'
      foreach ($e in $box.FindAll($TS::Descendants, $btnCond)) {
        $n = $e.Current.Name
        if (($n -like 'Send*' -or $n -like 'Stop*') -and (Ok $e)) {
          $r = $e.Current.BoundingRectangle
          $s = '{0} {1} {2} {3}' -f [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height
        }
      }
      $f = 0
      try { if ($edit.Current.HasKeyboardFocus -or ($edit.Current.ClassName -like '*ProseMirror-focused*')) { $f = 1 } } catch { }
      $parts += ('{0} {1} {2} {3} {4} {5}' -f [int]$b.X, [int]$b.Y, [int]$b.Width, [int]$b.Height, $s, $f)
    }
    if ($parts.Count -gt 0) { $out = [string]$cache.gen + '#' + ($parts -join ';') }
  } catch { $cache.hwnd = '' }
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
        """(generation, [{"box": (x, y, w, h), "btn": (x, y, w, h) or None, "focused": bool}, ...]) or None;
        generation changes whenever the set of boxes is replaced"""
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
        gen, _, line = line.partition("#")
        out = []
        for part in line.split(";"):
            try:
                v = [int(x) for x in part.split()]
            except ValueError:
                continue
            if len(v) == 9 and v[2] > 0:
                out.append({"box": tuple(v[:4]), "btn": tuple(v[4:8]) if v[6] > 0 else None, "focused": v[8] == 1})
        return (gen, out) if out else None


class ClaudeWatcher(threading.Thread):
    """Keeps an eye on the Claude app: whether its window is open, its message boxes (one per tab),
    which one you're typing in, and which ones are brand-new chats (the app's own little Clawd is
    sitting there). Publishes where Clawd should perch as `perch_rel`, and bumps `target_id` when
    that's a different box than before."""

    def __init__(self, q):
        super().__init__(daemon=True)
        self.q = q
        self.hwnd = find_claude_window()
        self.perch_rel = None  # perch point as (from the window's right edge, from its bottom edge)
        self.target_id = 0
        self.want = False      # set by the pet while it lives on (or is heading to) a message box
        self.force = False     # look for new chats right away
        self.locator = ComposerLocator()
        self.misses = 0
        self.newchat = {}
        self.last_check = 0.0
        self.target_key = None
        self.pending_key, self.pending_since = None, 0.0
        self.gen = None
        self.saved_rect, self.last_save = None, 0.0
        self.busy = False  # set by the pet while he's flying / being dragged

    @staticmethod
    def key(c):  # identity of a box that survives it growing upward while you type
        bx, by, bw, bh = c["box"]
        return round(bx / 40), round((by + bh) / 40)

    def set_target(self, k):
        self.target_key = k
        self.target_id += 1
        self.pending_key = None

    def check_new_chats(self, h, comps, keys):
        self.last_check = time.time()
        cap = capture_window(h)
        if not cap:
            return
        buf, W, H, wl, wt = cap
        sc = (ctypes.windll.user32.GetDpiForWindow(_VP(h)) or 96) / 96
        found = {}
        for c, k in zip(comps, keys):
            bx, by, bw, bh = c["box"]
            cx = c["btn"][0] + c["btn"][2] / 2 if c["btn"] else bx + bw - 20.3 * sc
            found[k] = orange_critter(buf, W, H, cx - 100 * sc - wl, by - 120 * sc - wt,
                                      cx + 60 * sc - wl, by + 2 * sc - wt, sc)
        self.newchat = found

    def pick(self, comps, keys, now):
        ok = [k for k in keys if not self.newchat.get(k, False)]
        focused = [k for c, k in zip(comps, keys) if c["focused"]]
        if focused and focused[0] in ok:
            want = focused[0]           # the box you're typing in...
        elif self.target_key in ok:
            want = self.target_key      # ...else stay put...
        elif ok:
            want = ok[-1]               # ...else any box that isn't a new chat
        else:
            want = None                 # only new chats (or nothing): nowhere to sit
        if want != self.target_key:
            if want is None or self.target_key is None or self.target_key not in ok:
                # nothing to hold on to (or his box just became a new chat): move now
                self.set_target(want)
            elif want != self.pending_key:  # moving to another box: make sure you meant it
                self.pending_key, self.pending_since = want, now
            elif now - self.pending_since >= 0.4:
                self.set_target(want)
        else:
            self.pending_key = None
        return keys.index(self.target_key) if self.target_key in keys else None

    def run(self):
        while True:
            front = False
            try:
                h = find_claude_window()
                if h and not self.hwnd:
                    self.q.put(("claude_opened", ""))
                elif self.hwnd and not h:
                    self.perch_rel = None
                    self.q.put(("claude_closed", ""))
                self.hwnd = h
                front = claude_in_front()
                if h and not is_iconic(h) and time.time() - self.last_save > 3:
                    self.last_save = time.time()
                    vr = window_rect(h, visible=True)
                    if vr and vr != self.saved_rect and vr[2] - vr[0] > 300 and vr[3] - vr[1] > 200:
                        self.saved_rect = vr
                        save_state(rect=list(vr))  # so opening it can grow straight into that spot
                if h and self.want and not is_iconic(h):
                    found = self.locator.locate(h)
                    r = window_rect(h)
                    if found and r:
                        gen, comps = found
                        self.misses = 0
                        now = time.time()
                        keys = [self.key(c) for c in comps]
                        # look for the app's own Clawd (new chats): when asked, when the boxes were
                        # replaced (switched / opened a tab), and otherwise every 1.5s
                        if not self.busy and (front or self.force) and (self.force or gen != self.gen or now - self.last_check > 1.5
                                                      or any(k not in self.newchat for k in keys)):
                            self.force = False
                            self.gen = gen
                            self.check_new_chats(h, comps, keys)
                        i = self.pick(comps, keys, now)
                        if i is None:
                            self.perch_rel = None
                        else:
                            c = comps[i]
                            bx, by, bw, bh = c["box"]
                            if c["btn"]:  # stand right above the Send / Stop button...
                                x = c["btn"][0] + c["btn"][2] / 2
                            else:         # ...or where it normally sits, near the box's right end
                                x = bx + bw - 20.3 * (ctypes.windll.user32.GetDpiForWindow(_VP(h)) or 96) / 96
                            self.perch_rel = (r[2] - x, r[3] - by)  # feet on the box's top border
                    else:  # mid tab-switch a box can vanish for a moment: hold on before giving up
                        self.misses += 1
                        if self.misses >= 5:
                            self.perch_rel = None
            except Exception as e:
                log("watcher error", repr(e))
            time.sleep(0.2 if (self.want and front) else 0.6)


def ease_out_back(u, k=1.4):
    u -= 1
    return 1 + (k + 1) * u * u * u + k * u * u


def lerp_rect(a, b, e):
    return tuple(a[i] + (b[i] - a[i]) * e for i in range(4))


# ---------------------------------------------------------------- the pet
class Pet:
    def __init__(self, root, q, watcher=None):
        self.root, self.q, self.watcher = root, q, watcher
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass
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
        self.skin_map = None
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
        self.wear_menu = tk.Menu(self.menu, tearoff=0)
        self.startup_var = tk.BooleanVar(value=startup_enabled())

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
        self.rel_s = None           # smoothed perch point (relative to the Claude window)
        self.vis, self.vis_raw, self.vis_since = False, False, 0.0
        self.leap = None            # arc from wherever he is onto the input box
        self.sm = None              # "summon Claude" sequence
        self.pan = None             # the window that grows out of him
        self.panel = self.panel_cv = None
        self.pan_last = None
        self.home = bool(watcher and watcher.hwnd)  # Claude is open: Clawd lives on its message box when he can
        self.return_at = now + random.uniform(10, 20)  # when he's on the screen edges: hop back onto the box after this
        self.lost_since = None      # perched, but the box went away / became a new chat
        self.ready_since = None     # on the screen edges, and a box is free to sit on
        self.seen_target = 0
        self.no_rise_until = 0.0
        self.no_side_until = 0.0
        self.last = now
        self.t = 0.0
        self.load_love()
        self.make_panel()  # made up front so opening Claude doesn't hitch
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
        if self.skin_map:
            col = self.skin_map.get(col, col)
        self.canvas.create_rectangle(int(x), int(y), int(x + w), int(y + h), fill=C.get(col, col), width=0)

    def pattern(self, cx, cy, pat, col, ps):
        h, w = len(pat), len(pat[0])
        x0, y0 = cx - w * ps / 2, cy - h * ps / 2
        for j, row in enumerate(pat):
            for i, ch in enumerate(row):
                if ch == "X":
                    self.rect(x0 + i * ps, y0 + j * ps, ps, ps, col, canon=False)

    def bubble_at(self, lines, ax, ay, side, cw, ch, color=None):
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
        self.rect(x + b, y, bw - 2 * b, bh, color or "T", canon=False)
        self.rect(x, y + b, bw, bh - 2 * b, color or "T", canon=False)
        self.rect(x + b, y + b, bw - 2 * b, bh - 2 * b, "W", canon=False)
        for i, s in enumerate(lines):
            self.canvas.create_text(x + bw / 2, y + pad + lh * i + lh / 2, text=s, font=self.font,
                                    fill=color or C["T"])

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
            elif eyes == "annoyed":  # unimpressed: flat half-closed lids
                self.rect(ex - S / 2, top + 2.5 * S, 2 * S, max(1, S / 3), "K")
                self.rect(ex, top + 3 * S, S, S, "K")
            elif eyes == "star":  # sparkly gacha eyes
                self.rect(ex, top + 2 * S, S, 2 * S, "S")
                self.rect(ex, top + 2 * S, S / 2 + 1, S / 2 + 1, "W")
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
        elif arms == "crossed":  # arms folded: attitude
            self.rect(ox + 3 * S, top + 5 * S, 8 * S, S, "D")
            self.rect(ox + 3 * S, top + 5 * S, S, S, "L")
            self.rect(ox + 10 * S, top + 5 * S, S, S, "L")
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
        self.draw_gear(ox, top)

    def draw_gear(self, ox, top):
        """whatever he's wearing from the gacha"""
        item = self.wear
        if item not in GEAR:
            return
        S = self.S
        dy = math.sin(self.t * 3) * 0.35 if item == "halo" else 0
        for x, y, w, h, col in GEAR[item]:
            self.rect(ox + x * S, top + (y + dy) * S, w * S, h * S, col)

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
        if _e is not None and self.mode == "idle":
            self.pull()  # every click on him is a gacha pull
            self.last_hover = time.time()
            return
        # "Pet Clawd" from the menu
        if self.jump < 1.5 * self.S:  # only hop from the ground, so spam-clicking can't stack hops
            self.jv = 9.0 * self.S
        self.pgoal = self.FULL
        self.say(random.choice(MOOD_LINES[self.mood()]["pet"]), 1.8)
        for _ in range(4):
            self.spawn("heart")
        self.add_love(1.5)
        self.meter_until = time.time() + 3
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
        self.return_at = float("inf")  # (set again once he lands)
        self.set_size(self.S_big)  # grows back in your hand
        self.mode = "drag"
        self.drag_hist.clear()
        self.particles = []
        self.hover = False
        self.action = None
        self.pending_edge = None
        self.sleeping = False
        self.say(random.choice(MOOD_LINES[self.mood()]["grab"]), 1.6)

    def throw(self):
        now = time.time()
        # being tossed around: fun when he likes you, rude when he doesn't
        self.add_love(0.5 if self.mood() in ("smitten", "happy") else -1.5)
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
        pt = self.perch_point() if self.watcher else None
        if pt and math.hypot(vx, vy) < 1800 * self.S / 5 and \
                math.hypot(self.fx - pt[0], self.fy - (pt[1] - 5 * self.S)) < 150 * self.scale:
            self.home = True  # dropped onto a message box: sit on it
            if self.start_leap():
                return
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
        self.return_at = now + random.uniform(10, 20)  # hang out on the edge a bit, then hop back onto Claude
        self.pending_edge = None
        self.hover = False
        self.bubble = None
        self.particles = []
        self._geom = None
        for _ in range(2):
            self.spawn("spark")
        if self.mood() in ("grumpy", "sulky"):  # "rude!!"
            for _ in range(3):
                self.spawn("steam")

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

    # ---------- love & gacha
    def load_love(self):
        st = load_state()
        now = time.time()

        def num(key, default, kind=float):
            try:
                return kind(st.get(key, default))
            except (TypeError, ValueError):
                return default
        away = max(0.0, now - num("saved_at", now))
        # he missed you while he wasn't running (gently, and capped)
        self.love = max(0.0, min(100.0, num("love", 60.0) - min(35.0, away / 3600 * 4)))
        self.pulls, self.pity_rare, self.pity_leg = num("pulls", 0, int), num("pity_rare", 0, int), num("pity_leg", 0, int)
        owned = st.get("owned", [])
        self.owned = {x for x in owned if x in ALL_LOOT} if isinstance(owned, list) else set()
        self.wear = st.get("wear") if st.get("wear") in self.owned else None
        self.skin = "golden" if (st.get("skin") == "golden" and "golden skin" in self.owned) else "classic"
        self.skin_map = SKINS[self.skin]
        self.last_touch = now
        self.love_dirty, self.next_love_save = True, now + 5
        self.click_times = collections.deque()
        self.meter_until = 0.0
        self.refusing, self.warm, self.warmed_at, self.refuse_look = False, 0.0, 0.0, 1
        self.eyes_fx, self.eyes_fx_until = None, 0.0

    def save_love(self):
        save_state(love=round(self.love, 2), saved_at=time.time(), pulls=self.pulls, pity_rare=self.pity_rare,
                   pity_leg=self.pity_leg, owned=sorted(self.owned), wear=self.wear, skin=self.skin)
        self.love_dirty = False

    def mood(self):
        L = self.love
        return "smitten" if L >= 80 else "happy" if L >= 55 else "meh" if L >= 30 else "grumpy" if L >= 10 else "sulky"

    def add_love(self, x):
        self.love = max(0.0, min(100.0, self.love + x))
        self.love_dirty = True
        if x > 0:
            self.last_touch = time.time()

    def tick_love(self, now, dt):
        if now - self.last_touch > 45 and self.love > 0:  # ignored: love slowly drains (~1% every 90s)
            self.love = max(0.0, self.love - dt / 90)
            self.love_dirty = True
        if self.love_dirty and now >= self.next_love_save:
            self.next_love_save = now + 20
            self.save_love()

    def roll(self):
        """one gacha roll; better luck the more he loves you, with pity so you can't go dry forever"""
        self.pulls += 1
        self.pity_rare += 1
        self.pity_leg += 1
        if self.pity_leg >= 60:
            r = "legendary"
        elif self.pity_rare >= 10:
            r = random.choices(["rare", "epic", "legendary"], [10, 4, 1])[0]
        else:
            luck = MOOD_LUCK[self.mood()]
            names = list(RARITY)
            r = random.choices(names, [RARITY[n][0] * (luck if n in LOOT else 1) for n in names])[0]
        if r in LOOT:
            self.pity_rare = 0
        if r == "legendary":
            self.pity_leg = 0
        self.love_dirty = True
        return r

    def pull(self):
        now = time.time()
        S = self.S
        mood = self.mood()
        lines = MOOD_LINES[mood]
        self.click_times.append(now)
        while self.click_times and now - self.click_times[0] > 8:
            self.click_times.popleft()
        self.meter_until = now + 3.0
        self.pgoal = self.FULL
        if len(self.click_times) > 6:  # spam-clicking: he's had enough
            self.say(random.choice(["ok ok OK", "stop poking!", "I get it!!", "...enough"]), 1.4)
            if mood in ("grumpy", "sulky"):
                self.add_love(-0.5)
            for _ in range(3):
                self.spawn("steam")
            return
        if mood in ("grumpy", "sulky") and now - self.warmed_at > 60:
            # attitude: if you haven't petted him in a while he might just ignore you
            if random.random() < (0.6 if mood == "sulky" else 0.3):
                self.say(random.choice(lines["refuse"]), 1.6)
                self.eyes_fx, self.eyes_fx_until = "annoyed", now + 1.6
                for _ in range(2):
                    self.spawn("steam")
                self.add_love(0.3)  # (attention is still attention)
                return
        r = self.roll()
        col = RARITY[r][1]
        if self.jump < 1.5 * S:
            self.jv = (12.0 if r == "legendary" else 9.0) * S
        if r == "common":
            self.say(random.choice(lines["click"]), 1.6)
            for _ in range(2):
                self.spawn("heart")
        elif r == "uncommon":
            if random.random() < 0.5:
                self.say("+ " + random.choice(lines["click"]) + " +", 1.8, color=col)
                for _ in range(9):
                    self.spawn("heart")
            else:
                self.eyes_fx, self.eyes_fx_until = "star", now + 1.8
                self.say("sparkle eyes!", 1.8, color=col)
                for _ in range(4):
                    self.spawn("spark", col=col)
        else:
            item = random.choice(LOOT[r])
            new = item not in self.owned
            self.owned.add(item)
            if item == "golden skin":
                self.skin, self.skin_map = "golden", SKINS["golden"]
            else:
                self.wear = item
            banner = {"rare": "* RARE *", "epic": "** EPIC **", "legendary": "*** LEGENDARY ***"}[r]
            self.say(f"{banner}\n{item}" + ("  NEW!" if new else "  (dupe)"), 3.0 if r == "legendary" else 2.4, color=col)
            for _ in range({"rare": 6, "epic": 10, "legendary": 18}[r]):
                self.spawn("spark", col=col)
            if r == "legendary":
                self.eyes_fx, self.eyes_fx_until = "star", now + 3.0
        self.add_love(RARITY[r][2] * (0.5 if mood in ("grumpy", "sulky") else 1.0))

    def set_wear(self, item):
        self.wear = item
        self.save_love()

    def set_skin(self, skin):
        self.skin, self.skin_map = skin, SKINS[skin]
        self.save_love()

    def build_menu(self):
        m, w = self.menu, self.wear_menu
        m.delete(0, "end")
        w.delete(0, "end")
        m.add_command(label=f"\u2665 Love {int(self.love)}%  \u00b7  {self.mood()}", state="disabled")
        m.add_command(label=f"Pulls {self.pulls}  \u00b7  collection {len(self.owned)}/{len(ALL_LOOT)}", state="disabled")
        w.add_command(label=("\u2713 " if self.wear is None else "     ") + "nothing", command=lambda: self.set_wear(None))
        for item in ALL_LOOT:
            if item in self.owned and item in GEAR:
                w.add_command(label=("\u2713 " if self.wear == item else "     ") + item,
                              command=lambda i=item: self.set_wear(i))
        if "golden skin" in self.owned:
            w.add_separator()
            for sk in ("classic", "golden"):
                w.add_command(label=("\u2713 " if self.skin == sk else "     ") + sk + " skin",
                              command=lambda s_=sk: self.set_skin(s_))
        m.add_cascade(label="Wear", menu=w, state="normal" if self.owned else "disabled")
        m.add_separator()
        m.add_command(label="Pet Clawd", command=lambda: self.q.put(("hi", "")))
        m.add_command(label="Test permission alert", command=lambda: self.q.put(("alert", "Bash")))
        m.add_command(label="Sit on Claude's input box", command=lambda: self.q.put(("perch", "")))
        m.add_command(label="Hide for 10 minutes", command=self.go_away)
        m.add_checkbutton(label="Start with Windows", variable=self.startup_var, command=self.toggle_startup)
        m.add_separator()
        m.add_command(label="Quit", command=self.quit_clawd)

    def quit_clawd(self):
        try:
            self.save_love()
        finally:
            self.root.destroy()

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
        if now_ and self.S != s:
            self.apply_size(s)

    def step_size(self, now, dt):
        """glide toward the target size (in flight / in your hand) instead of popping between sizes"""
        d = self.size_target - self.S
        if d:
            self.apply_size(self.size_target if abs(d) < 0.05 else self.S + d * min(1.0, dt * 7))

    # ---------- perching on the Claude app's input box
    def perch_point(self):
        """screen point on the top border of Claude's message box, above the Send button (or None)"""
        w = self.watcher
        if not w or not w.hwnd or not w.perch_rel or is_iconic(w.hwnd):
            return None
        r = window_rect(w.hwnd)
        if not r:
            return None
        rel = self.rel_s or w.perch_rel
        return r[2] - rel[0], r[3] - rel[1]

    def ease_perch(self, dt):
        """glide (instead of teleporting) when the box grows, shrinks or moves inside the window"""
        tgt = self.watcher.perch_rel if self.watcher else None
        if not tgt:
            return
        if self.rel_s is None:
            self.rel_s = tgt
            return
        k = 1 - math.exp(-dt * 14)
        self.rel_s = (self.rel_s[0] + (tgt[0] - self.rel_s[0]) * k, self.rel_s[1] + (tgt[1] - self.rel_s[1]) * k)

    def request_perch(self, secs=None):
        self.home = True
        self.return_at = 0.0  # right away
        if self.watcher:
            self.watcher.want = True
            self.watcher.force = True

    def go_sides(self):
        """the box went away (or it's a new chat with the app's own Clawd): wait on the side of the screen"""
        now = time.time()
        pt = self.perch_pt
        l, t, r, b = work_area()
        cx = pt[0] if pt else (l + r) / 2
        cy = pt[1] if pt else t + (b - t) * 0.6
        self.perched = False
        self.lost_since = self.ready_since = None
        self.rel_s = None
        self.set_size(self.S_big, now_=True)
        self.edge = "left" if cx - l < r - cx else "right"
        L, m = self.edge_len(), self.CW * 0.55
        self.pos = self.target = max(m, min(L - m, cy - t - 60 * self.scale))
        self.p, self.pgoal = float(self.HIDE), float(self.EYES)  # pops back into view, peeking
        self.jump = self.jv = 0.0
        self.action, self.action_until = "peek", now + 2.5
        self.next_action = now + random.uniform(4, 6)
        self.pending_edge = None
        self.return_at = now + random.uniform(10, 20)
        self._geom = None

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
        self.rel_s = self.watcher.perch_rel
        self.seen_target = self.watcher.target_id
        self.lost_since = self.ready_since = None
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
        e = 0.8 * u + 0.2 * u * u * (3 - 2 * u)  # (almost) constant sideways speed, like a real jump
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
            mood = self.mood()
            self.action, self.pgoal = "popup", (self.EYES if mood == "sulky" else self.FULL)
            self.wave = mood in ("smitten", "happy") and random.random() < 0.6
            self.action_until = now + random.uniform(1.8, 3.0)
            if mood in ("smitten", "grumpy") and random.random() < 0.3:
                self.say(random.choice(MOOD_LINES[mood]["pop"]), 2.0)
        elif r < 0.8:
            self.action, self.pgoal = "duck", self.HANDS  # just his hands on the box
            self.action_until = now + random.uniform(2, 4)
        else:
            self.action, self.pgoal = "sleep", int(3.2 * S)
            self.sleeping = True
            self.action_until = now + random.uniform(6, 10)
        self.next_action = self.action_until + random.uniform(1.5, 3.5)

    # ---------- summoning the Claude app: Clawd inflates into its window
    def start_summon(self):
        self.set_size(self.S_big, now_=True)
        open_claude()  # start the app the instant you let go - it loads while he inflates
        self.mode = "summon"
        self.sm = {"phase": "windup", "t0": time.time(), "t_open": time.time()}
        self.particles = []
        self.set_hit(None)
        self.say("opening Claude!", 1.0)

    def tick_summon(self, now, dt):
        S, F, sm = self.S, self.FW, self.sm
        if sm["phase"] == "windup":  # coast to a stop with a little squash...
            k = math.exp(-dt * 10)
            self.vx *= k
            self.vy *= k
            self.fx += self.vx * dt
            self.fy += self.vy * dt
            el = now - sm["t0"]
            self.place(F, F, int(self.fx - F / 2), int(self.fy - F / 2))
            self.canvas.delete("all")
            self.free = ("bottom", F / 2, F / 2 + math.sin(min(1.0, el / 0.18) * math.pi) * S * 0.6)
            self.draw_body(0, 0, "happy", 0, "wave", -1, blush=True)
            self.free = None
            if self.bubble and now < self.bubble[1]:
                self.bubble_at(self.bubble[0], F / 2, F / 2 - 6 * S, "up", F, F)
            if el > 0.18:  # ...then inflate into the window (drawn on the big overlay from here on)
                self.canvas.delete("all")
                self.start_panel((self.fx - 5 * S, self.fy - 5 * S, self.fx + 5 * S, self.fy + 3 * S))
                sm.update(phase="inside")
            return
        self.canvas.delete("all")
        pn = self.pan
        if pn and pn["phase"] == "grow" and now - sm["t_open"] > 16:  # the app never showed up
            sm["failed"] = True
            self.pan_fade()
        if pn is None:
            if sm.get("failed") and self.pan_last:  # shrink-free fallback: drop back out
                x0, y0, x1, y1 = self.pan_last
                self.fx, self.fy = (x0 + x1) / 2, (y0 + y1) / 2
                self.vx, self.vy = 0.0, -3.0 * S
                self.grav = "bottom"
                self.mode = "fall"
                return
            # he's inside the app now: he'll pop up out of its message box once it's ready
            self.mode = "idle"
            self.perched = True
            self.home = True
            self.set_size(self.S_small, now_=True)
            self.edge = "bottom"
            self.p, self.pgoal = float(self.HIDE), float(self.EYES)
            self.jump = self.jv = 0.0
            self.vis = self.vis_raw = False
            self.lost_since = self.ready_since = None
            self.no_side_until = now + 6  # the app can take a few seconds to show its message box
            self.rel_s = None
            self.perch_pt = self.perch_point()
            self.seen_target = self.watcher.target_id if self.watcher else 0
            self.action, self.next_action = None, now + 3
            self._geom = None

    def make_panel(self):
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.title("clawd-pet")
        win.attributes("-topmost", True)
        win.attributes("-transparentcolor", KEY)
        win.config(bg=KEY)
        no_activate(win)
        cv = tk.Canvas(win, bg=KEY, highlightthickness=0, bd=0)
        cv.pack(fill="both", expand=True)
        self.panel, self.panel_cv = win, cv

    def start_panel(self, body):
        l, t, r, b = work_area()
        W, H = r - l, b - t
        self.panel.geometry(f"{W}x{H}+{l}+{t}")  # one full-screen, mostly see-through window: never resized
        self.panel.attributes("-alpha", 1.0)
        self.panel.deiconify()
        self.root.lift()
        self.hit.lift()
        st = load_state()
        tw, th = int(W * 0.7), int(H * 0.76)
        to = (l + (W - tw) // 2, t + (H - th) // 2, l + (W + tw) // 2, t + (H + th) // 2)
        rr = st.get("rect")
        if (isinstance(rr, list) and len(rr) == 4 and rr[2] - rr[0] > 300 and rr[3] - rr[1] > 200
                and rr[0] < r and rr[2] > l and rr[1] < b and rr[3] > t):
            to = tuple(rr)  # grow straight into where Claude's window will appear
        try:
            E = max(0.7, min(3.5, float(st.get("launch", 1.6)) * 0.9))  # land about when the app shows up
        except (TypeError, ValueError):
            E = 1.4
        self.pan = {"phase": "grow", "t0": time.time(), "from": body, "to": to, "cur": body,
                    "origin": (l, t), "m": 0.0, "limb": 1.0, "E": E}

    def pan_fade(self):
        if self.pan and self.pan["phase"] != "fade":
            self.pan.update(phase="fade", t0=time.time())

    def note_launch(self, now):
        """remember how long Claude took to show its window, so next time the animation lands on time"""
        t_open = (self.sm or {}).get("t_open")
        if t_open and 0.2 < now - t_open < 15:
            try:
                old = float(load_state().get("launch", now - t_open))
            except (TypeError, ValueError):
                old = now - t_open
            save_state(launch=round(old * 0.5 + (now - t_open) * 0.5, 2))

    def watch_paint(self, pn):
        """background check: has the app drawn itself yet? (so we never reveal a blank window)"""
        def run():
            end = time.time() + 3.0
            while time.time() < end and self.pan is pn:
                if window_painted(pn["hwnd"]):
                    break
                time.sleep(0.12)
            pn["painted"] = True
        threading.Thread(target=run, daemon=True).start()

    def tick_panel(self, now):
        pn = self.pan
        if pn is None:
            return
        el = now - pn["t0"]
        if pn["phase"] == "grow" and now >= pn.get("next_find", 0):
            # look for the real window every 50ms, right from the first frame
            pn["next_find"] = now + 0.05
            h = find_claude_window()
            if h and not is_iconic(h):
                rr = window_rect(h, visible=True)
                if rr and rr[2] - rr[0] > 300 and rr[3] - rr[1] > 200:
                    # already right on it? then no settling needed; otherwise a quick glide, longer if further
                    dev = max(abs(a - b_) for a, b_ in zip(pn["cur"], rr))
                    snap_t = 0.0 if (dev < 12 * self.scale and pn["m"] > 0.9) else 0.12 + min(0.2, dev / 2500)
                    pn.update(phase="snap", t0=now, hwnd=h, m0=pn["m"], limb0=pn["limb"], painted=False,
                              snap_t=snap_t, **{"from": pn["cur"]})
                    self.watch_paint(pn)
                    self.request_perch()
                    self.note_launch(now)
                    el = 0.0
        if pn["phase"] == "grow":  # his body stretches into the window, timed to land as the app appears
            u = min(1.0, el / pn["E"])
            e = 1 - (1 - u) ** 3  # quick start, gentle arrival
            pn["cur"] = lerp_rect(pn["from"], pn["to"], e)
            pn["limb"] = max(0.0, 1 - e * 2.2)
            pn["m"] = smooth((e - 0.35) / 0.65)
        elif pn["phase"] == "snap":  # the real window is up: settle exactly onto it (following it if it moves)
            rr = window_rect(pn["hwnd"], visible=True) or pn["to"]
            k = smooth(el / pn["snap_t"]) if pn["snap_t"] > 0 else 1.0
            pn["cur"] = lerp_rect(pn["from"], rr, k)
            pn["m"] = pn["m0"] + (1 - pn["m0"]) * k
            pn["limb"] = pn["limb0"] * (1 - k)
            if el >= pn["snap_t"]:
                pn.update(phase="wait", t0=now, to=rr)
        elif pn["phase"] == "wait":  # sitting exactly on it until the app has drawn itself
            pn["cur"] = window_rect(pn["hwnd"], visible=True) or pn["to"]
            if pn.get("painted") or el > 3.0:
                pn.update(phase="fade", t0=now)
        elif pn["phase"] == "fade":  # melt away to reveal it
            if pn.get("hwnd"):
                pn["cur"] = window_rect(pn["hwnd"], visible=True) or pn["cur"]
            u = min(1.0, el / 0.3)
            self.panel.attributes("-alpha", max(0.0, 1 - smooth(u)))
            if u >= 1:
                self.panel.withdraw()
                self.pan_last = pn["cur"]
                self.pan = None
                return
        self.pan_last = pn["cur"]
        self.draw_panel(pn)

    def draw_panel(self, pn):
        S, cv = self.S, self.panel_cv
        ox, oy = pn["origin"]
        x0, y0, x1, y1 = (pn["cur"][0] - ox, pn["cur"][1] - oy, pn["cur"][2] - ox, pn["cur"][3] - oy)
        m, limb = pn["m"], pn["limb"]
        cv.delete("all")
        w, h = x1 - x0, y1 - y0
        if w < 4 or h < 4:
            return
        cw, ch = w / 10, h / 8  # his 10x8 body grid, stretched to the current size
        bg = "#262624"

        def box(a0, b0, a1, b1, col):
            cv.create_rectangle(a0, b0, a1, b1, fill=col, width=0)

        if limb > 0.01:  # arms and legs, tucking in
            for c in (1, 3, 6, 8):
                box(x0 + c * cw, y1, x0 + (c + 1) * cw, y1 + 2 * ch * limb, C["D"])
            box(x0 - 2 * cw * limb, y0 + 4 * ch, x0, y0 + 5 * ch, C["O"])
            box(x0 - 2 * cw * limb, y0 + 5 * ch, x0, y0 + 6 * ch, C["D"])
            box(x1, y0 + 4 * ch, x1 + 2 * cw * limb, y0 + 5 * ch, C["O"])
            box(x1, y0 + 5 * ch, x1 + 2 * cw * limb, y0 + 6 * ch, C["D"])
        for j, row in enumerate(BODY):  # the body, fading from Claude orange to window dark
            i = 0
            while i < len(row):
                k = i
                while k < len(row) and row[k] == row[i]:
                    k += 1
                box(x0 + i * cw, y0 + j * ch, x0 + k * cw, y0 + (j + 1) * ch, mix(C[row[i]], bg, m))
                i = k
        if m > 0:
            bw = max(2, S // 2) * m  # an orange window border grows in
            box(x0, y0, x1, y0 + bw, C["O"])
            box(x0, y1 - bw, x1, y1, C["O"])
            box(x0, y0, x0 + bw, y1, C["O"])
            box(x1 - bw, y0, x1, y1, C["O"])
            if h > 14 * S and m > 0.5:  # title bar strip
                box(x0 + bw + S, y0 + bw, x1 - bw - S, y0 + 5 * S, mix(bg, "#30302E", (m - 0.5) * 2))
            c = S * m  # chunky pixel corners
            for (a, b_) in ((x0, y0), (x1 - c, y0), (x0, y1 - c), (x1 - c, y1 - c)):
                box(a, b_, a + c, b_ + c, KEY)
        # his eyes slide together and become the loading dots
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        hold = m >= 0.95  # (the dots bounce once he's fully a window)
        for idx, col in ((0, 2), (2, 7)):
            ew, eh = min(cw, 6 * S), min(2 * ch, 12 * S)
            ex, ey = x0 + (col + 0.5) * cw - ew / 2, y0 + 3 * ch - eh / 2
            dx = cx + (idx - 1) * 3 * S - S / 2
            bob = max(0.0, math.sin(self.t * 7 - idx * 0.8)) * 1.5 * S if hold else 0
            dy = cy + 3 * S - bob
            fx, fy = ex + (dx - ex) * m, ey + (dy - ey) * m
            fw, fh = ew + (S - ew) * m, eh + (S - eh) * m
            if pn["phase"] != "fade":
                box(fx, fy, fx + fw, fy + fh, mix(C["K"], C["O"], m))
        if pn["phase"] != "fade" and m > 0.9:
            bob = max(0.0, math.sin(self.t * 7 - 0.8)) * 1.5 * S if hold else 0
            box(cx - S / 2, cy + 3 * S - bob, cx + S / 2, cy + 4 * S - bob, C["O"])
            late = pn["phase"] == "wait" or (pn["phase"] == "grow" and time.time() - pn["t0"] > pn["E"])
            if late and w > 44 * S and h > 26 * S:  # only if the app is taking its time
                cv.create_text(cx, cy - 3 * S, text="opening Claude", font=self.panel_font,
                               fill=mix(bg, C["O"], (m - 0.9) * 10))

    def on_menu(self, e):
        self.build_menu()
        self.meter_until = time.time() + 3
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

    def say(self, text, secs, color=None):
        self.bubble = (text.split("\n"), time.time() + secs, color)

    def spawn(self, kind, absolute=None, col=None, vy=None, at=None):
        S = self.S
        if absolute:
            x, y = absolute
        elif at:
            x, y = at
        else:
            x = self.GX + 7 * S + random.uniform(-5, 5) * S
            y = self.CH - self.p - self.jump + random.uniform(0, 3) * S
        self.particles.append({
            "x": x, "y": y, "vx": random.uniform(-1, 1) * S * 2,
            "vy": -random.uniform(5, 9) * S if vy is None else vy,
            "life": 1.0, "kind": kind, "abs": absolute is not None, "col": col,
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
                # Claude app opened / new project / menu: live on the app's message box
                if self.mode == "away":
                    self.come_back()
                if cmd == "session":
                    # a new chat shows the app's own little Clawd: step aside until it's gone
                    self.no_rise_until = time.time() + 1.5
                    if self.perched and self.mode == "idle":
                        self.pgoal = self.HIDE
                        self.sink_fast_until = time.time() + 0.3
                if cmd == "claude_opened" or (self.watcher and self.watcher.hwnd):
                    self.request_perch()
            elif cmd == "claude_closed":
                self.perch_wanted_until = 0
                self.home = False
                if self.perched and self.mode == "idle":
                    self.unperch(fall=True)
                elif self.perched:
                    self.perched = False
            elif cmd == "quit":
                self.quit_clawd()

    def come_back(self):
        self.mode = "idle"
        self.return_at = time.time() + random.uniform(10, 20)
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
                self.return_at = now + random.uniform(10, 20)
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
            mood = self.mood()
            if mood == "sulky":  # sulking: stays tucked away
                self.action, self.pgoal = "peek", self.EYES
                self.action_until = now + random.uniform(2.5, 4.5)
            else:
                self.action = "popup"
                self.pgoal = self.FULL
                self.wave = mood in ("smitten", "happy")
                self.action_until = now + random.uniform(2.2, 3.2)
                if random.random() < (0.7 if mood in ("smitten", "grumpy") else 0.5):
                    self.say(random.choice(MOOD_LINES[mood]["pop"]), 2.2)
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
        front = claude_in_front()
        pt = self.perch_point() if (self.perched or self.home) else None
        if self.perched:
            if pt:
                self.perch_pt = pt  # follows the box around
            if not (self.watcher and self.watcher.hwnd):
                self.unperch(fall=True)
                if self.mode != "idle":
                    return
            else:
                # only show himself while you're actually on Claude; debounced so flicking between
                # apps or tabs quickly doesn't make him bob up and down
                raw = pt is not None and front and now >= self.no_rise_until
                if raw != self.vis_raw:
                    self.vis_raw, self.vis_since = raw, now
                if raw and now - self.vis_since >= 0.12:
                    self.vis = True
                elif not raw and now - self.vis_since >= 0.35:
                    self.vis = False
                perch_visible = self.vis
                if pt and self.watcher.target_id != self.seen_target:  # you're typing in another box
                    self.seen_target = self.watcher.target_id
                    if perch_visible and self.p > self.HIDE + S and not self.hover:
                        if self.start_leap():  # hop over to it
                            return
                    else:
                        self.rel_s = None  # out of sight: just move there
                if front and pt is None:
                    # no box to sit on (a new chat, or a page without one): after a bit, go wait on
                    # the side of the screen - not straight away, in case you're just flicking past
                    if self.lost_since is None:
                        self.lost_since = now
                    elif now - self.lost_since >= 2.0 and now >= self.no_side_until and not self.hover:
                        self.go_sides()
                        return
                else:
                    self.lost_since = None
        elif (self.home and not self.hover and self.pending_alert is None and self.watcher and self.watcher.hwnd
              and now >= self.return_at):
            # been on the screen edge a while: once a box is free (and stays free a moment), hop back on
            if pt and front and now >= self.no_rise_until:
                if self.ready_since is None:
                    self.ready_since = now
                    self.pgoal = self.FULL  # get ready: pop up and eye the box
                    self.action, self.action_until = "eyeing", now + 1.5
                    self.sleeping = self.wave = False
                    self.pending_edge = None
                    self.target = self.pos
                elif now - self.ready_since >= 0.8:
                    self.ready_since = None
                    if self.start_leap():
                        return
            else:
                self.ready_since = None
        # hover
        box = self.sprite_screen_box()
        self.set_hit(box)
        px, py = self.root.winfo_pointerxy()
        m = 2 * S if self.hover else 0  # a little stickier once you're petting him
        inside = box is not None and box[0] - m <= px <= box[2] + m and box[1] - m <= py <= box[3] + m
        inside = inside and (perch_visible or not self.perched)
        if inside:
            mood = self.mood()
            if not self.hover:
                self.hover = True
                self.target = self.pos
                self.pending_edge = None
                self.sleeping = False
                self.wave = False
                self.action = "petted"
                # with attitude he won't take pets right away - keep at it and he gives in
                self.refusing = mood in ("grumpy", "sulky") and now - self.warmed_at > 60
                self.warm = 0.0
                self.refuse_look = random.choice((-1, 1))
                if self.refusing:
                    self.say(random.choice(MOOD_LINES[mood]["refuse"]), 1.6)
                elif random.random() < 0.4:
                    self.say(random.choice(MOOD_LINES[mood]["pet"]), 1.5)
            self.last_hover = now
            if self.refusing:
                self.warm += dt
                self.pgoal = self.FULL if mood == "grumpy" else self.EYES  # sulky: just peeks at you warily
                if random.random() < 0.05:
                    self.spawn("steam")
                if self.warm >= (1.6 if mood == "grumpy" else 3.2):
                    self.refusing = False
                    self.warmed_at = now
                    self.say(random.choice(["...fine.", "ok maybe a little", "hmph... ok", "...don't stop"]), 1.8)
                    self.add_love(1.0)
            else:
                self.pgoal = self.FULL
                self.add_love(dt * 0.6 * (1 - self.love / 130))  # petting slowly fills his heart
                if now > self.next_heart:
                    self.spawn("heart")
                    self.next_heart = now + 0.35
                if self.jump == 0 and random.random() < 0.025:
                    self.jv = 5.0 * S
        elif self.hover and now - self.last_hover > 0.08:
            self.hover = False
            self.refusing = False
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
            if self.action in ("peek", "popup", "sleep", "cheer", "landed", "duck", "eyeing") and now > self.action_until:
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
        mood = self.mood()
        if self.p > self.EYES - S and not self.hover:
            if mood == "smitten" and random.random() < 0.008:  # can't help it
                self.spawn("heart")
            elif mood == "sulky" and random.random() < 0.08:  # his little rain cloud
                top_ = self.CH - self.p - self.jump
                self.spawn("rain", at=(self.GX + 7 * S + random.uniform(-2, 2) * S, top_ - 3 * S), vy=6 * S)
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
        mood = self.mood()
        sassy = mood in ("grumpy", "sulky") and not (self.hover and not self.refusing)
        if now < self.eyes_fx_until:
            eyes = self.eyes_fx
        elif self.sleeping:
            eyes = "sleep"
        elif self.hover and self.refusing:
            eyes = "annoyed"
        elif self.hover or self.action in ("cheer", "landed"):
            eyes = "happy"
        elif now < self.blink_until:
            eyes = "blink"
        elif sassy:
            eyes = "annoyed"
        else:
            eyes = "open"
        look = self.refuse_look if (self.hover and self.refusing) else (self.look if eyes in ("open", "annoyed") else 0)
        hand_y = self.CH - 2 * S - min(0, self.p)
        gripping = top + 4 * S > hand_y
        if gripping:
            arms = ("grip", hand_y)
        elif self.wave or self.action == "cheer":
            arms = "wave"
        else:
            arms = "crossed" if sassy else "normal"
        legs_frame = int(self.t * 8) % 2 if (self.walking or self.hover) else -1
        blush = (self.hover and not self.refusing) or self.action == "cheer" or mood == "smitten"
        if self.p > self.HIDE + 1 or not gripping:
            self.draw_body(self.GX, top, eyes, look, arms, legs_frame, blush=blush)
        if self.pending_alert is not None and self.hover and int(self.t * 3) % 2 == 0:
            self.rect(self.GX + 15 * S, top + 0 * S, S, 3 * S, "R")
            self.rect(self.GX + 15 * S, top + 4 * S, S, S, "R")
        visible = self.p > self.EYES - S
        hat = max(0, -min(g[1] for g in GEAR[self.wear])) if self.wear in GEAR else 0  # headgear height
        meter = visible and (now < self.meter_until or (self.hover and not self.refusing))
        if meter:
            self.draw_meter(top - hat * S)
        elif visible and mood == "sulky" and not self.hover:
            ax, ay = self.Tp(self.GX + 7 * S, top - (4 + hat) * S)
            self.pattern(ax, ay, CLOUD, "#7D8597", max(2, S * 0.7))
        # bubble
        if self.bubble:
            lines, until = self.bubble[0], self.bubble[1]
            color = self.bubble[2] if len(self.bubble) > 2 else None
            if now > until:
                self.bubble = None
            elif visible:
                ax, ay = self.Tp(self.GX + 7 * S, top - ((5 if meter else 1) + hat) * S)
                side = {"bottom": "up", "top": "down", "left": "right", "right": "left"}[self.edge]
                w, h = self.canvas_size()
                self.bubble_at(lines, ax, ay, side, w, h, color)
        self.draw_particles(dt)

    def draw_meter(self, top):
        """his love as 5 little pixel hearts over his head"""
        S = self.S
        ps = max(2, S * 0.5)
        filled = int(self.love / 20 + 0.5)
        for i in range(5):
            ax, ay = self.Tp(self.GX + 7 * S + (i - 2) * 6 * ps, top - 2.5 * S)
            self.pattern(ax, ay, HEART, "H" if i < filled else "#5A5250", ps)

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
            col, kind = pt.get("col"), pt["kind"]
            if kind == "heart":
                self.pattern(ax, ay, HEART, col or "H", ps)
            elif kind == "spark":
                self.pattern(ax, ay, SPARK, col or "S", ps)
            elif kind == "sweat":
                self.pattern(ax, ay, DROP, "B", max(2, S // 2))
            elif kind == "steam":
                self.pattern(ax, ay, STEAM, col or "#CFCFCF", max(2, S // 2))
            elif kind == "rain":
                self.pattern(ax, ay, DROP, col or "#7FB2E5", max(2, S // 3))
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
                self.watcher.busy = self.mode in ("drag", "fall", "leap", "summon")
                self.watcher.want = (self.perched or self.mode in ("leap", "summon")
                                     or (self.home and now >= self.return_at - 1.0))
            self.tick_panel(now)
            self.step_size(now, dt)
            self.tick_love(now, dt)
            if self.perched or self.mode == "leap":
                self.ease_perch(dt)
            else:
                self.rel_s = None
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
        period = 1 / 60 if fast else 1 / 30
        self.root.after(max(1, int((period - (time.time() - now)) * 1000)), self.tick)


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
