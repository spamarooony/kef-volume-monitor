# Features

A detailed inventory of what this project actually does, grouped by component. For *why* things are built this way, see [ARCHITECTURE.md](ARCHITECTURE.md); for known gaps/caveats, see the [README's Known Limitations](README.md#known-limitations).

## Bridge server (`kef_server.py`)

- **Protocol translation**: speaks the speakers' raw undocumented TCP protocol (port 50001, 3/4-byte commands) so the browser only ever needs plain HTTP.
- **Background polling with caching**: a single background thread polls the speaker every 3s and caches volume/mute in memory; browser requests to `/volume` read the cache instead of hitting the speaker directly, keeping simultaneous connections to the speaker's TCP stack low.
- **Disk-backed state persistence** (`kef_state.json`): last-known volume/mute is written on real change and reloaded on startup, so a service restart doesn't drop back to "unknown."
- **Online/offline detection with self-healing**: a failed poll marks the speaker offline; the next 3s cycle retries automatically with no manual intervention.
- **Quiet logging**: only startup and online↔offline transitions are logged (Werkzeug's per-request access log is silenced), so routine 24/7 operation produces no log spam.
- **REST endpoints**:
  - `GET /volume` - cached volume/mute/online state, plus `display_version` (display page's file mtime, for client auto-reload)
  - `POST /volume/set` - sends a volume change straight to the speaker synchronously (not gated by the poll loop), rejects if the speaker is offline
  - `GET /status` - basic server/speaker-IP health check
  - `GET /`, `GET /display`, `GET /manifest.json` - serves the HTML pages and PWA manifest from the same origin (avoids CORS/`file://` issues)
- **CORS enabled** (`flask-cors`) for cross-origin access if needed.
- **Configurable via CLI flags** (`--ip`, `--port`) rather than hardcoded values.
- **systemd-friendly**: designed to run as a `Restart=always` systemd service; see [SETUP.md](SETUP.md).
- **Git-pull deploys**: server runs the repo as a git clone, so shipping an update is `git push` + `git pull` on the server (service restart only needed if `kef_server.py` itself changed).

## Volume controller (`kef_volume.html`, served at `/`)

- **Live readout**: large numeral, progress bar, and slider reflecting current volume, polling `/volume` every 750ms.
- **Direct control**: +/− step buttons (1% per press) and a draggable slider, posting changes to `/volume/set`.
- **Debounced commits**: rapid slider drags are queued and coalesced rather than firing a request per pixel of movement.
- **Safety ceiling with unlock**: volume is capped at a safe 45% by default; a toggle unlocks the full 0-100 range, and re-locking clamps any louder-than-safe volume back down immediately.
- **Mute indication**: reflects the speaker's mute state visually (distinct from volume being at 0).
- **Offline/stale handling**: controls disable and the readout dims when the speaker is unreachable or the last update is stale, instead of silently showing wrong data.
- **Accent color theming**: six preset swatches plus a custom hex input, persisted in `localStorage` and re-applied on load.
- **Accessible controls**: ARIA labels/roles on the slider and buttons (`role`, `aria-valuemin/max/now/text`, `aria-label`) for screen-reader use.

## Wall display (`kef_display.html`, served at `/display`)

- **TV-OSD-style readout**: shows current volume large enough to read from across a room, styled like a TV's native volume overlay.
- **Idle fade-to-black**: fades out after a configurable timeout (2s/5s/10s/30s presets, matching a normal TV's OSD) or can be set to always-on; wakes instantly on tap or on a genuine server-driven volume change.
- **Drag-and-resize layout editor**: an edit mode where the volume numeral and bar widgets can be freely dragged and resized; layout persists across reloads (`localStorage`) and rescales as a group when the viewport or rotation changes.
- **Bar widget orientation toggle**: the volume bar can be rotated between horizontal and vertical independent of the numeral.
- **Bar widget remove/restore**: the volume bar can be hidden (shown as a ghost placeholder in edit mode) and brought back without losing its saved position/size.
- **Reset-to-home layout**: one action reverts the current layout back to the last explicitly saved arrangement.
- **Manual screen rotation**: pure-CSS 0°/90°/180°/270° rotation of the whole display, independent of (and working around) the OS's own auto-rotate/orientation-lock quirks on mobile; persisted across reloads.
- **Fullscreen mode with orientation lock**: toggles fullscreen and attempts to hold the requested orientation even if the browser tries to auto-rotate on entering fullscreen.
- **Auto-reload on file change**: the display page polls its own file mtime via the server and reloads itself automatically when `kef_display.html` is updated on disk, so a deployed wall display picks up changes without manual intervention.
- **Screen-wake-lock workaround**: plays a silent (to the user) looping video to exploit Firefox for Android's audio-tab screen-timeout exemption, keeping the display awake without the HTTPS-only real Wake Lock API (Chrome has no equivalent, so this is Firefox-only - see [README](README.md#known-limitations)).
- **Accent color theming**: same preset/custom-hex system as the controller, stored under its own `localStorage` key so display and controller can use different colors.
- **Installable as a PWA**: `manifest.json` lets the display be added to a phone's home screen as a standalone black-background app pointed at `/display`.
- **Responsive to viewport changes**: reconciles widget layout when the browser chrome shows/hides or the window resizes, not just on rotation.

## Protocol layer (shared)

- **Raw byte-level speaker protocol**: 3-byte `GET` and 4-byte `SET` commands for volume (0-100) and mute (top bit of the response byte), read directly from `aiokef`'s source rather than guessed.
- **Plain HTTP by design**: no TLS anywhere in the stack, a deliberate tradeoff to avoid certificate management on a home LAN (see [SETUP.md](SETUP.md)); this is also why the real Wake Lock API isn't available, hence the video workaround above.
