"""Poke Clawd from a Claude Code hook: notify.py alert|done|clear|session|hi|quit
Starts the pet if it isn't running (for alert/done/hi)."""
import json
import os
import socket
import subprocess
import sys

PORT = 47863
HERE = os.path.dirname(os.path.abspath(__file__))


def detail_from_stdin():
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return ""
    tool = data.get("tool_name")
    if tool:
        return str(tool).replace("mcp__", "")[:22]
    msg = str(data.get("message", ""))
    if " use " in msg:
        return msg.rsplit(" use ", 1)[1].strip()[:22]
    return ""


def send(line):
    with socket.create_connection(("127.0.0.1", PORT), timeout=0.5) as s:
        s.sendall(line.encode("utf-8"))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "alert"
    detail = detail_from_stdin() if cmd == "alert" else ""
    try:
        send(f"{cmd}|{detail}\n")
    except OSError:
        if cmd in ("alert", "done", "hi", "session"):
            exe = sys.executable
            if exe.lower().endswith("python.exe"):
                exe = exe[:-10] + "pythonw.exe"
            subprocess.Popen(
                [exe, os.path.join(HERE, "pet.py"), cmd, detail],
                creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | NEW_PROCESS_GROUP
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )


if __name__ == "__main__":
    main()
