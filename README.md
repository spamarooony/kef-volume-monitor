# KEF LS50 Wireless (Gen 1) Web-Based Volume Monitor

## Intro + Goal
This idea started with the occasional need to see the speaker's volume, without having to pick up the phone to check the KEF Control app. The goal: check and change the volume of first-generation KEF LS50 Wireless speakers from a website, without a remote in hand. While working out the infrastructure and architecture for that, I built two files: `kef_volume.html` and `kef_display.html`. Both can be opened on any device on the network, with `kef_display.html` for monitoring the volume and `kef_volume.html` for controlling it. They can be loaded on an old phone and used as a dedicated volume monitor/controller.

## The Challenge
The gen 1 LS50 Wireless has no official HTTP or REST API. Unlike the gen 2 models, it only accepts raw byte commands over a plain TCP socket on port 50001 (a protocol KEF never documented publicly, reverse-engineered instead by the community behind [`aiokef`](https://github.com/basnijholt/aiokef)). This meant a browser (which can only speak HTTP) couldn't talk to the speakers directly; something in between was needed to translate.

## Architecture

```
Browser  ←── HTTP (port 8765) ──→  kef_server.py  ←── TCP (port 50001) ──→  KEF Speakers
(any device)                       (Ubuntu machine)                         (<speaker-ip>)
```

**1. The speakers** listen on TCP port 50001. A 3-byte request `[0x47, 0x25, 0x80]` ("G", "%", 128) returns a 4-byte response whose 4th byte encodes volume (0–100) and mute state (top bit). Setting volume sends `[0x53, 0x25, 0x81, vol]`.

**2. `kef_server.py`** (a small Python Flask server running on an always-on Ubuntu 24.04 machine as a `systemd` service). It:
- Runs a single background thread that polls the speakers every 3 seconds (opening a fresh raw TCP `socket`, reading volume/mute, and closing the connection immediately), and caches the result in memory. Browser requests to `/volume` just read that cache; they never touch the speaker directly. This keeps the connection to the speaker's control port brief and infrequent regardless of how many browser tabs are watching, since the speaker's TCP stack can't handle many simultaneous connections (confirmed: it visibly conflicts with the official KEF Control app if held open too long or hit too often).
- Persists the last-known volume/mute state to `kef_state.json` on disk (written on every real change, loaded on startup), so a service restart doesn't lose it the way the in-memory cache alone would. Only speaker sleep did before.
- Logs quietly: only startup and online↔offline transitions are logged, not every poll or every browser request. Routine operation produces no log spam even running 24/7.
- Exposes `/volume` (GET, cached read) and `/volume/set` (POST, sends the change to the speaker immediately/synchronously, not gated by the poll loop).
- Serves the HTML pages themselves at `/` and `/display`, plus `/manifest.json`, so everything lives on the same origin and avoids browser CORS/file:// restrictions.

**3. `kef_volume.html`** (a single-page dashboard styled as a dark, minimal readout with a large volume number, a progress bar, a slider, and +/− buttons, stepping by 1). It polls `/volume` every 3 seconds and posts to `/volume/set` when the user adjusts volume.

**4. `kef_display.html`** (served at `/display`), a read-only, full-screen volume readout meant for a phone mounted near the TV, mimicking a TV's own volume OSD: it shows the current volume large enough to read from across the room, fades to black after a short idle timeout, and wakes instantly on tap or on a genuine volume change. Layout (position/size of the numeral and bar) is drag/resize-editable and persists, rescaling as one group whenever the phone is rotated. See [Known Limitations](#known-limitations) for the screen-wake-lock caveat.

## Setup

On the Ubuntu (or any Linux) machine that can reach the speakers:

```bash
python3 -m venv kef-env
kef-env/bin/pip install flask flask-cors
kef-env/bin/python kef_server.py --ip 192.168.1.XXX   # your speaker's IP
```

Then open `http://<server-ip>:8765/` in a browser on the same network.

To run it persistently, install it as a systemd service so it starts on boot and restarts if it ever crashes:

```ini
# /etc/systemd/system/kef-server.service
[Unit]
Description=KEF volume bridge server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<user>
WorkingDirectory=/path/to/kef-volume-monitor
ExecStart=/path/to/kef-volume-monitor/kef-env/bin/python kef_server.py --ip 192.168.1.XXX
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kef-server.service
```

## Repo → server workflow

The Ubuntu server runs this repo as a git clone (pulled read-only via a deploy key scoped to just this repo). To ship a change:

```bash
git commit -am "..." && git push
ssh <user>@<server-ip> "cd /path/to/kef-volume-monitor && git pull"
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

## Known Limitations

**Speakers cannot be turned on remotely over the network on early units.** This is a hardware/firmware limitation of early-production LS50 Wireless pairs, not a bug in this project or the underlying `aiokef` protocol. Confirmed directly: every port that responds while the speakers are on (control port 50001, embedded web UI on 80, UPnP on 8080/1900) becomes completely unreachable (`no route to host`) the moment they're switched off, rather than staying in a low-power listening state, so there's no command, from this dashboard or otherwise, that can reach a fully powered-down unit. Waking them requires physically pressing the power button.

According to KEF's own [firmware release notes](https://assets.kef.com/pdf_doc/ls50w/LS50-Wireless-Firmware-Release-Note.pdf), wake-up via the KEF Control app, Spotify Connect, and DLNA/network is only supported on units with a serial number **at or after `LS50W13074K24L/R2G`**. To check your own pair: compare the digits in your speakers' serial number (printed on the back, format `LS50Wnnnnn...`) against that threshold. Earlier serials lack network wake-up entirely; later ones support it.

As a result, the dashboard can only ever report "offline" (last known volume shown dimmed, controls disabled) while an affected pair is off. It has no way to bring them back online itself.

**`kef_display.html`'s screen-wake-lock hack only works in Firefox.** The real Screen Wake Lock API needs HTTPS, which this project deliberately doesn't require (see the deploy workflow above, it's plain HTTP by design). Several non-HTTPS workarounds were tested directly on the target device: Chrome for Android has no working technique at all (the old "keep a video playing" trick seems to have been removed); Firefox for Android still honors it, but only for a video with a genuinely *unmuted* soundtrack. Silent or muted video gets no exemption. The page plays such a video to satisfy that requirement; whether it stays awake depends only on the video being unmuted at the page level, not on the phone's actual output volume. Firefox's check is "is this tab producing audio," not "is that audio audible." In other words, turn media volume down to not hear the tone. The screen will stay awake either way. Net effect: the mounted phone must run Firefox for the screen to stay awake indefinitely; on Chrome, the phone will follow its own OS screen-timeout and need a tap/unlock to wake, same as normal use. Tested only on Android with Firefox and Chrome. I don't have an iPhone so I can't speak to how it behaved there.

## Final Result
- A live, auto-refreshing volume readout accessible from any device on the home network at `http://<server-ip>:8765`
- Slider and +/− buttons to adjust volume directly from the page, applied to the speaker immediately
- A second, read-only display page (`/display`) for a phone mounted near the TV, mimicking a TV's own volume OSD - see above
- Runs as a `systemd` service (`kef-server.service`) so it starts automatically on boot and restarts if it ever crashes
- Source of truth is this GitHub repo; the server pulls updates rather than manual file copying

## Possible Future Directions Discussed
- Running the same dashboard on a small WiFi-connected ESP32 AMOLED touch display, talking to the speakers directly over TCP with no Ubuntu server involved. If that pans out, look into publishing it on [APP PIXELS](https://www.app-pixels.com/), an app store/catalog for Waveshare ESP32-S3 AMOLED devices
- Running the whole stack (server + browser) locally on a repurposed old Android phone via Termux, as a fully self-contained wall-mounted display (the current `/display` page still relies on the Ubuntu server and a regular mobile browser, not a standalone on-phone stack)

## License
[PolyForm Noncommercial 1.0.0](LICENSE), free for personal, hobby, educational, and nonprofit use; commercial use is not permitted.
