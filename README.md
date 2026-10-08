# Clawd

A tiny pixel desktop pet for Windows that lives on the edges of your screen and taps you on the shoulder when Claude Code needs your permission.

## What it does

- **Hides** behind a screen edge, usually with just its two little hands holding on.
- **Wanders** along the edges, hangs out in corners, hops between edges, and peeks out now and then.
- **Pops up** sometimes to wave or say hi, and naps occasionally.
- **Hover** to pet it: it climbs out, gets happy eyes, and hearts float up. Move your mouse away and it ducks back down right away.
- **Click** it and it hops.
- **Right-click** for a menu: test alert, hide for 10 minutes, start with Windows, quit.
- **Permission alerts:** when Claude Code asks for permission, Clawd drops down from the top-center of the screen and dangles there until you respond. Click it to jump to the Claude window.
- **Done cheer:** when Claude finishes a reply, Clawd pops up and cheers.
- Stays quiet if you're already looking at the Claude desktop app or a claude.ai tab.

## Requirements

- Windows 10/11
- Python 3.10+ with tkinter (the standard python.org installer includes it)

## Setup

1. Clone this repo somewhere permanent, e.g. `C:\Users\<you>\.claude\claude-pet`.
2. Start it: `pythonw pet.py`
3. Right-click Clawd and tick **Start with Windows** if you want it at login.
4. Add the hooks from [`hooks.example.json`](hooks.example.json) to `~/.claude/settings.json`. Change the Python and repo paths to match your machine.

`notify.py` is what the hooks call. It sends a message to the running pet over `127.0.0.1:47863` and starts the pet if it isn't running yet.

```
python notify.py alert   # dangle from the top of the screen
python notify.py clear   # dismiss the alert
python notify.py done    # celebrate
python notify.py hi      # say hi
python notify.py quit    # exit
```

## Files

| File | Purpose |
|---|---|
| `pet.py` | The pet: a transparent, always-on-top Tk window drawn as pixel art |
| `notify.py` | Small client used by the hooks |
| `hooks.example.json` | Claude Code hook configuration |

Errors are logged to `pet.log` next to the script.
