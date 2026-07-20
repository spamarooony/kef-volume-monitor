# KEF LS50 Wireless (Gen 1) Web-Based Volume Monitor

## Goal
Check and change the volume of first-generation KEF LS50 Wireless speakers from a website, without a remote in hand.

## The Challenge
The gen 1 LS50 Wireless has no official HTTP or REST API. Unlike the gen 2 models, it only accepts raw byte commands over a plain TCP socket on port 50001 (an undocumented, community-reverse-engineered protocol). This meant a browser (which can only speak HTTP) couldn't talk to the speakers directly; something in between was needed to translate.

## Architecture

```
Browser  ←──── HTTP (port 8765) ────→  kef_server.py  ←──── TCP (port 50001) ────→  KEF Speakers
(any device)                           (Ubuntu machine)                              (<speaker-ip>)
```

**1. The speakers** listen on TCP port 50001. A 3-byte request `[0x47, 0x25, 0x80]` ("G", "%", 128) returns a 4-byte response whose 4th byte encodes volume (0–100) and mute state (top bit). Setting volume sends `[0x53, 0x25, 0x81, vol]`.

**2. `kef_server.py`** (a small Python Flask server running on an always-on Ubuntu 24.04 machine as a `systemd` service). It:
- Runs a single background thread that polls the speakers every 3 seconds (opening a fresh raw TCP `socket`, reading volume/mute, and closing the connection immediately), and caches the result in memory. Browser requests to `/volume` just read that cache; they never touch the speaker directly. This keeps the connection to the speaker's control port brief and infrequent regardless of how many browser tabs are watching, since the speaker's TCP stack can't handle many simultaneous connections (confirmed: it visibly conflicts with the official KEF Control app if held open too long or hit too often).
- Logs quietly: only startup and online↔offline transitions are logged, not every poll or every browser request. Routine operation produces no log spam even running 24/7.
- Exposes `/volume` (GET, cached read) and `/volume/set` (POST, sends the change to the speaker immediately/synchronously, not gated by the poll loop).
- Serves the HTML page itself at `/`, so the page and the server live on the same origin and avoid browser CORS/file:// restrictions.

**3. `kef_volume.html`** (a single-page dashboard styled as a dark, minimal readout with a large volume number, a progress bar, a slider, and +/− buttons, stepping by 1). It polls `/volume` every 3 seconds and posts to `/volume/set` when the user adjusts volume.

## Setup

On the Ubuntu (or any Linux) machine that can reach the speakers:

```bash
python3 -m venv kef-env
kef-env/bin/pip install flask flask-cors
kef-env/bin/python kef_server.py --ip 192.168.1.XXX   # your speaker's IP
```

Then open `http://<server-ip>:8765/` in a browser on the same network.

To run it persistently, install it as a systemd service (`Restart=always`) so it survives reboots and crashes. See `kef-server.service` on the deployment machine for the unit definition used in production.

## Repo → server workflow

The Ubuntu server runs this repo as a git clone (pulled read-only via a deploy key scoped to just this repo). To ship a change:

```bash
git commit -am "..." && git push
ssh <user>@<server-ip> "cd /path/to/kef-volume && git pull"
# restart the service only if kef_server.py changed:
ssh <user>@<server-ip> "sudo systemctl restart kef-server.service"
```

## Key Problems Solved Along the Way

| Problem | Cause | Fix |
|---|---|---|
| `externally-managed-environment` pip error | Ubuntu 24.04 blocks system-wide pip installs | Used a Python virtual environment (`python3 -m venv`) |
| Server exited silently with no output | File got truncated mid-paste in `nano` | Rewrote the file using `cat > file << 'EOF'` heredoc, which avoids paste truncation |
| `ModuleNotFoundError: setuptools` | Missing dependency pulled in by `aiokef` | `pip install setuptools` |
| `'AsyncKefSpeaker' object has no attribute 'get_muted'` | Wrong method name assumed | Inspected the installed library directly (`dir(AsyncKefSpeaker)`) to find the real method: `get_volume_and_is_muted()` |
| Requests worked once then hung/timed out | `aiokef`'s async connection got tangled across Flask's per-request event loops | Bypassed the library entirely and spoke the raw TCP protocol directly with Python's built-in `socket` module, one clean connect/send/receive/close per poll |
| Page couldn't reach the server at all | HTML file opened as a local `file://` page (browsers block cross-origin network requests from local files) | Served the HTML from the Flask server itself, so the page loads over `http://` from the same origin as the API |
| **Setting volume crashed/rebooted the speakers** | An incorrect, guessed byte sequence was sent (extra trailing byte not part of the real protocol) | Read the exact command bytes from the actual `aiokef` source code (`_SET_START, _VOL, _SET_MID, volume` = 4 bytes, no extra byte) |
| Speakers occasionally refuse connections while the KEF Control app is in use | The speaker's embedded TCP stack can only handle a small number of simultaneous connections on port 50001 ([confirmed in `aiokef` issue tracker](https://github.com/basnijholt/aiokef/issues/15)) | Poll loop keeps connections brief (connect → read → close) and self-heals on the next 3s cycle if a connection is refused, no manual intervention needed, dashboard just misses one update |
| journald filling with routine request logs | Flask/Werkzeug logs every single `GET`/`POST` by default | Silenced Werkzeug's access logger; app now only logs actual online↔offline transitions |

## Final Result
- A live, auto-refreshing volume readout accessible from any device on the home network at `http://<server-ip>:8765`
- Slider and +/− buttons to adjust volume directly from the page, applied to the speaker immediately
- Runs as a `systemd` service (`kef-server.service`) so it starts automatically on boot and restarts if it ever crashes
- Source of truth is a private GitHub repo; the server pulls updates via a read-only deploy key rather than manual file copying

## Possible Future Directions Discussed
- Running the same dashboard on a small WiFi-connected ESP32 AMOLED touch display, talking to the speakers directly over TCP with no Ubuntu server involved
- Running the whole stack (server + browser) locally on a repurposed old Android phone via Termux, as a fully self-contained wall-mounted display
