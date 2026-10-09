"""Poke Clawd from a Claude Code hook: notify.py note|clear|seen|session|alert|done|hi|quit

note   - reads the hook's JSON on stdin and turns it into a notification Clawd delivers as a gacha
         capsule: permission, question, plan, error, done, waiting or info
clear  - a tool ran (so whatever that session was asking permission for is settled)
seen   - you sent that session a message (all of its notifications are settled)
Starts the pet if it isn't running (for note/alert/done/hi/session)."""
import json
import os
import re
import socket
import subprocess
import sys

PORT = 47863
HERE = os.path.dirname(os.path.abspath(__file__))

# Notification hook types -> kind of capsule
NOTIFICATION_KINDS = {
    "permission_prompt": "permission",
    "elicitation_dialog": "question",
    "elicitation_url_dialog": "question",
    "agent_needs_input": "question",
    "idle_prompt": "waiting",
    "agent_completed": "done",
}
ERRORS = {
    "rate_limit": "hit a rate limit",
    "overloaded": "servers are overloaded",
    "authentication_failed": "login problem",
    "oauth_org_not_allowed": "login problem",
    "account_on_hold": "account is on hold",
    "billing_error": "billing problem",
    "invalid_request": "invalid request",
    "model_not_found": "model not found",
    "server_error": "server error",
    "max_output_tokens": "reply got too long",
    "cloud_credential_error": "cloud credentials problem",
}


def hook_input():
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        data = json.loads(sys.stdin.read() or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def plain(text, n):
    """markdown-ish text -> one short plain line"""
    text = re.sub(r"```.*?```", " ", str(text or ""), flags=re.S)
    text = re.sub(r"^#+ .*$", " ", text, flags=re.M)  # headings
    text = re.sub(r"[`*#>|]+", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


def describe(tool, ti):
    """what a tool wants to do, in a few words"""
    tool = str(tool or "")
    ti = ti if isinstance(ti, dict) else {}
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        return plain(": ".join(p for p in parts[1:3] if p), 60)
    for key in ("command", "file_path", "notebook_path", "url", "pattern", "description"):
        val = ti.get(key)
        if isinstance(val, str) and val.strip():
            if key.endswith("path"):
                val = os.path.basename(val)
            return plain(f"{tool}: {val.strip().splitlines()[0]}", 60)
    return tool


def note_from(data):
    """hook JSON -> {"kind", "text"}, "clear" or None"""
    ev = data.get("hook_event_name", "")
    tool, ti = data.get("tool_name"), data.get("tool_input") or {}
    if ev == "PermissionRequest":
        if tool == "AskUserQuestion":
            qs = ti.get("questions") if isinstance(ti, dict) else None
            first = qs[0] if isinstance(qs, list) and qs and isinstance(qs[0], dict) else {}
            return {"kind": "question", "text": plain(first.get("question", ""), 160)}
        if tool == "ExitPlanMode":
            return {"kind": "plan", "text": ""}
        return {"kind": "permission", "text": describe(tool, ti)}
    if ev == "Notification":
        ntype = data.get("notification_type", "")
        msg = str(data.get("message", ""))
        if ntype in ("elicitation_complete", "elicitation_response"):
            return "clear"  # you answered it
        kind = NOTIFICATION_KINDS.get(ntype, "info")
        if kind == "permission":
            used = msg.rsplit(" use ", 1)[1].strip() if " use " in msg else ""
            if used == "AskUserQuestion":
                return {"kind": "question", "text": ""}
            if used == "ExitPlanMode":
                return {"kind": "plan", "text": ""}
            return {"kind": "permission", "text": plain(used.replace("mcp__", ""), 60)}
        if kind in ("waiting", "done"):
            return {"kind": kind, "text": ""}
        return {"kind": kind, "text": plain(msg, 160)}
    if ev == "Stop":
        return {"kind": "done", "text": plain(data.get("last_assistant_message", ""), 160)}
    if ev == "StopFailure":
        err = str(data.get("error", ""))
        return {"kind": "error", "text": plain(ERRORS.get(err) or data.get("error_details") or err, 160)}
    return None


def send(line):
    with socket.create_connection(("127.0.0.1", PORT), timeout=0.5) as s:
        s.sendall(line.encode("utf-8"))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "note"
    data = hook_input() if cmd in ("note", "alert", "done", "clear", "seen") else {}
    sid = str(data.get("session_id") or "")[:64]
    note = None
    if cmd == "note":
        note = note_from(data)
        if note is None:
            return
        if note == "clear":
            cmd, note = "clear", None
    elif cmd == "alert":  # older hook setups
        note = {"kind": "permission", "text": describe(data.get("tool_name"), data.get("tool_input"))}
    elif cmd == "done":
        note = {"kind": "done", "text": plain(data.get("last_assistant_message", ""), 160)}
    if note is not None:
        note["sid"] = sid
        cmd, detail = "note", json.dumps(note)
    elif cmd in ("clear", "seen"):
        detail = sid
    else:
        detail = ""
    try:
        send(f"{cmd}|{detail}\n")
    except OSError:
        if cmd in ("note", "hi", "session"):
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
