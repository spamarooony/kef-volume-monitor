# KEF LS50 Wireless (Gen 1) Web-Based Volume Monitor

## Goal
Check the volume level of first-generation KEF LS50 Wireless speakers from a website, without a physical volume knob or remote in hand.

## The Challenge
The gen 1 LS50 Wireless has no official HTTP or REST API. Unlike the gen 2 models, it only accepts raw byte commands over a plain TCP socket on port 50001 (an undocumented, community-reverse-engineered protocol). This meant a browser (which can only speak HTTP) couldn't talk to the speakers directly; something in between was needed to translate.

## Architecture

```
Browser  ←──── HTTP (port 8765) ────→  kef_server.py  ←──── TCP (port 50001) ────→  KEF Speakers
(any device)                           (Ubuntu machine)                              (<speaker-ip>)
```

**1. The speakers** listen on TCP port 50001. A 3-byte request `[0x47, 0x25, 0x80]` ("G", "%", 128) returns a 4-byte response whose 4th byte encodes volume (0–100) and mute state (top bit).

**2. `kef_server.py`** (a small Python Flask server running on an always-on Ubuntu 24.04 machine). It:
- Opens a fresh TCP socket to the speakers on each request (this proved more reliable than trying to keep a persistent async connection alive, which repeatedly hung after the first call)
- Exposes `/volume` (GET) to read the current level and mute state
- Exposes `/volume/set` (POST) to change the volume
- Serves the HTML page itself at `/`, so the page and the server live on the same origin and avoid browser CORS/file:// restrictions

**3. `kef_volume.html`** (a single-page dashboard styled as a dark, minimal readout with a large volume number, a progress bar, a slider, and +/− buttons, stepping by 1). It polls `/volume` every 3 seconds and posts to `/volume/set` when the user adjusts volume.

## Key Problems Solved Along the Way

| Problem | Cause | Fix |
|---|---|---|
| `externally-managed-environment` pip error | Ubuntu 24.04 blocks system-wide pip installs | Used a Python virtual environment (`python3 -m venv`) |
| Server exited silently with no output | File got truncated mid-paste in `nano` | Rewrote the file using `cat > file << 'EOF'` heredoc, which avoids paste truncation |
| `ModuleNotFoundError: setuptools` | Missing dependency pulled in by `aiokef` | `pip install setuptools` |
| `'AsyncKefSpeaker' object has no attribute 'get_muted'` | Wrong method name assumed | Inspected the installed library directly (`dir(AsyncKefSpeaker)`) to find the real method: `get_volume_and_is_muted()` |
| Requests worked once then hung/timed out | aiokef's async connection got tangled across Flask's per-request event loops | Bypassed the library entirely and spoke the raw TCP protocol directly with Python's built-in `socket` module, one clean connect/send/receive/close per request |
| Page couldn't reach the server at all | HTML file opened as a local `file://` page (browsers block cross-origin network requests from local files) | Served the HTML from the Flask server itself, so the page loads over `http://` from the same origin as the API |
| **Setting volume crashed/rebooted the speakers** | An incorrect, guessed byte sequence was sent (extra trailing byte not part of the real protocol) | Read the exact command bytes from the actual `aiokef` source code installed on disk (`_SET_START, _VOL, _SET_MID, volume` = 4 bytes, no extra byte), replaced the guess with values taken directly from a working, tested library |
| Firewall / reachability doubts | N/A (turned out not to be the issue) | Verified with `/status` endpoint test from the second device before chasing the wrong fix |

## Final Result
- A live, auto-refreshing volume readout accessible from any device on the home network at `http://<server-ip>:8765`
- Slider and +/− buttons to adjust volume directly from the page
- Runs as a `systemd` service (`kef-server.service`) so it starts automatically on boot and restarts if it ever crashes, with no manual intervention needed

## Possible Future Directions Discussed
- Only polling the speakers while the page is actually open, rather than continuously (implemented, polling now happens on-demand per browser request rather than via a constant background loop)
- Running the same dashboard on a small WiFi-connected ESP32 AMOLED touch display, talking to the speakers directly over TCP with no Ubuntu server involved
- Running the whole stack (server + browser) locally on a repurposed old Android phone via Termux, as a fully self-contained wall-mounted display
