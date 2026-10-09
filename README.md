# Clawd

A tiny pixel desktop pet for Windows that lives on the edges of your screen and delivers every Claude Code notification to you as a gacha capsule.

> [!WARNING]
> **Use at your own risk: Clawd is an experimental, untested hobby project. Expect bugs.**
>
> There is no test suite and no QA. It has only been tried on one Windows 11 machine. It may glitch, get stuck, land in odd places, miss notifications, or misbehave with multiple monitors, unusual taskbar setups, or display scaling. It is not affiliated with or endorsed by Anthropic. If something goes wrong, right-click Clawd and choose **Quit**, or end `pythonw.exe` in Task Manager.

## What it does

- **Hides** behind a screen edge, usually with just its two little hands holding on.
- **Wanders** along the edges, hangs out in corners, hops between edges, and peeks out now and then.
- **Pops up** sometimes to wave or say hi, and naps occasionally.
- **Hover** to pet it: it climbs out, gets happy eyes, and hearts float up. Move your mouse away and it ducks back down right away.
- **Click** it and it hops.
- **Drag** it off its edge: it squirms in your grip. Let go and it falls the way you threw it. Gravity pulls it toward the nearest edge, and if you let go in the middle of the screen it just falls down. It turns to face the wall it's about to hit, lands, slides along the edge with leftover momentum, and slips straight back into hiding.
- **Love & moods:**
  - Clawd has a love meter (0-100%, shown as 5 pixel hearts over his head). Petting him (hovering) slowly fills it, clicking him adds a little, and ignoring him drains it. It's saved in `state.json`.
  - His mood follows his love: smitten (blushing, floating hearts), happy, meh, grumpy (unimpressed eyes, crossed arms, steam, sassy lines, refuses pets until you keep at it) and sulky (a little rain cloud, hides from you, ignores clicks). Spam-clicking annoys him, and throwing him around is fun when he likes you and rude when he doesn't.
- **Skins & accessories:** right-click > **Skin** picks his colours (classic, golden, mint, berry, ocean, grape, midnight, ghost) and **Accessory** puts something on him (crown, party hat, bow, flower, sunglasses, halo). Your pick is remembered.
- **Right-click** for a menu: his love and mood, skin, accessory, test a notification (any rarity), hide for 10 minutes, start with Windows, quit.
- **Gacha notifications:** every Claude Code notification comes as a gacha capsule. Clawd ducks out of sight, drops down from the top-center of the screen (by the camera) hanging on with one hand and holding the capsule out with the other, rattles it, and cracks it open (or hover to open it right away). The more Claude needs you, the rarer the pull:

  | Rarity | Notification |
  |---|---|
  | LEGENDARY (gold, rays of light, sparkly eyes) | Claude needs your permission, with what it wants to run |
  | EPIC (purple) | Claude has a question for you, or its plan is ready for review |
  | CURSED (red) | Claude hit an error (rate limit, overloaded, login...) |
  | RARE (blue) | Claude finished, with the start of its reply |
  | UNCOMMON (green) | Claude has been waiting on you for a while |
  | common (grey) | anything else Claude wants to tell you |

  - He dangles there until you deal with it: answer in Claude, or click him to jump to the Claude window. Answering a permission prompt or question, or sending that session a message, settles it on its own.
  - Several at once: he shows the rarest and counts the rest ("+2 more"). A rarer one arriving drops in as a fresh capsule.
  - If you're petting him when one arrives, he cracks the capsule open right there in his hands.
  - Stays quiet if you're already looking at the Claude desktop app or a claude.ai tab.
- **Sits on Claude's message box:** whenever the Claude app opens, or a new project/session starts, Clawd hops onto the app's message box, shrinking to a tiny size on the way, and perches right above the Send button, peeking over it.
  - It glides along with the box as it grows while you type, and when you move or resize the window, and ducks behind the box when you switch to another app.
  - With several tabs open (several message boxes), it hops to the one you're typing in.
  - It never sits on a brand-new chat, where the app's own little Clawd is. If that's all there is, it waits on the side of the screen and hops back on once the app's Clawd is gone. It waits a couple of seconds before leaving, so flicking quickly between tabs doesn't send it bouncing around.
  - Drag it off (it grows back to full size in your hand) and throw it to an edge: it hangs out there doing its thing for 10-20 seconds, then pops up and hops back onto the box. The same goes any time it ends up on the screen edges while Claude is open. Drop it right above a message box to put it straight back. (Right-click > *Sit on Claude's input box* also calls it back.)
- **Summons Claude:** if the Claude app isn't open, drop Clawd in the middle of the screen. He inflates into a window (his arms and legs tuck in, his body turns into the window and his eyes into its loading dots) and melts away to reveal it. The app starts the moment you let go, and Clawd remembers where Claude's window lives and how long it takes to open, so he inflates straight into that spot and lands about as it appears. Then he pops up out of its message box.
- **Opens Claude for you:** when Clawd starts and neither the Claude desktop app nor Claude Code is running, it launches the Claude app. Clicking Clawd also opens the app if no Claude window can be found.

## Requirements

- Windows 10/11
- Python 3.10+ with tkinter (the standard python.org installer includes it)

## Setup

1. Clone this repo somewhere permanent, e.g. `C:\Users\<you>\.claude\claude-pet`.
2. Start it from the repo folder with `pythonw pet.py`, or from anywhere with the full path, e.g. `pythonw C:\Users\<you>\.claude\claude-pet\pet.py`. (`pythonw` never shows errors, so if nothing happens, check the path.) The first time it runs, it adds **Clawd** to your Start menu.
3. Right-click Clawd and tick **Start with Windows** if you want it at login.
4. Add the hooks from [`hooks.example.json`](hooks.example.json) to `~/.claude/settings.json`. Change the Python and repo paths to match your machine.

### Reopening Clawd after quitting

- Press the Windows key, type **Clawd**, and hit Enter.
- He also comes back by himself the next time Claude sends a notification or you start a new Claude Code session.
- Starting him while he's already running just makes him pop up and say hi (it also brings him back early from *Hide for 10 minutes*).

`notify.py` is what the hooks call. It reads the hook's JSON, works out what kind of notification it is, sends it to the running pet over `127.0.0.1:47863`, and starts the pet if it isn't running yet.

```
python notify.py note    # (from a hook) deliver this notification as a capsule
python notify.py clear   # (from a hook) a tool ran: that session's permission prompt is settled
python notify.py seen    # (from a hook) you messaged that session: all its notifications are settled
python notify.py alert   # permission capsule (also works by hand)
python notify.py done    # "Claude's done" capsule
python notify.py hi      # say hi
python notify.py quit    # exit
```

To try every rarity without waiting for Claude, right-click Clawd > **Test a notification**.

## Files

| File | Purpose |
|---|---|
| `pet.py` | The pet: a transparent, always-on-top Tk window drawn as pixel art |
| `notify.py` | Small client used by the hooks |
| `hooks.example.json` | Claude Code hook configuration |

Errors are logged to `pet.log` next to the script.
