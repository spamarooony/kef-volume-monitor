#!/usr/bin/env python3
import argparse
import json
import logging
import os
import socket
import threading
import time
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SPEAKER_IP = "<server-ip>"  # placeholder - override with --ip
SPEAKER_PORT = 50001
SERVER_PORT = 8765
POLL_INTERVAL = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("werkzeug").setLevel(logging.ERROR)
log = logging.getLogger("kef")

app = Flask(__name__)
CORS(app)

speaker_ip = DEFAULT_SPEAKER_IP

cache = {"online": False, "volume": 0.0, "muted": False, "error": "Starting up"}
cache_lock = threading.Lock()

STATE_FILE = os.path.join(APP_DIR, "kef_state.json")
_last_persisted = {"volume": None, "muted": None}  # tracks what's on disk, so poll_loop only writes on actual change

def load_state():
    # Only ever seeds volume/muted - online and error are always runtime-derived, never restored.
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        with cache_lock:
            cache["volume"] = float(data["volume"])
            cache["muted"] = bool(data["muted"])
        _last_persisted["volume"] = cache["volume"]
        _last_persisted["muted"] = cache["muted"]
        log.info(f"loaded last-known state from {STATE_FILE}: volume={cache['volume']:.2f}, muted={cache['muted']}")
    except Exception as e:
        log.info(f"no usable prior state ({e}), starting with defaults")

def save_state(volume, muted):
    # Never allowed to raise - a persistence failure must not crash the server or block a poll/set cycle.
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"volume": volume, "muted": muted}, f)
        _last_persisted["volume"] = volume
        _last_persisted["muted"] = muted
    except Exception as e:
        log.warning(f"failed to persist state: {e}")

# Protocol constants taken directly from aiokef source
_GET_START = ord("G")  # 0x47
_SET_START = ord("S")  # 0x53
_VOL       = ord("%")  # 0x25
_GET_END   = 128       # 0x80
_SET_MID   = 129       # 0x81

GET_VOLUME_CMD = bytes([_GET_START, _VOL, _GET_END])         # [0x47, 0x25, 0x80]
def set_volume_cmd(vol): return bytes([_SET_START, _VOL, _SET_MID, vol])  # [0x53, 0x25, 0x81, vol]

def tcp_call(ip, data):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect((ip, SPEAKER_PORT))
        s.sendall(data)
        return s.recv(1024)

def parse_volume(response):
    if len(response) < 4:
        raise ValueError(f"Short response: {response.hex()}")
    val = response[3]
    muted = bool(val & 0x80)
    volume = (val & 0x7f) / 100.0
    return volume, muted

def poll_loop():
    was_online = None  # None = unknown yet (startup), True/False = last observed state
    while True:
        try:
            response = tcp_call(speaker_ip, GET_VOLUME_CMD)
            volume, muted = parse_volume(response)
            with cache_lock:
                cache.update({"online": True, "volume": volume, "muted": muted, "error": None})
            if volume != _last_persisted["volume"] or muted != _last_persisted["muted"]:
                save_state(volume, muted)  # picks up changes made by other clients (e.g. a physical remote)
            if was_online is False:
                log.info(f"speaker back online (volume={volume:.2f}, muted={muted})")
            was_online = True
        except Exception as e:
            with cache_lock:
                cache.update({"online": False, "error": str(e)})
            if was_online is not False:
                log.warning(f"speaker offline: {e}")
            was_online = False
        time.sleep(POLL_INTERVAL)

@app.route("/")
def index():
    return send_from_directory(APP_DIR, "kef_volume.html")

@app.route("/display")
def display():
    return send_from_directory(APP_DIR, "kef_display.html")

@app.route("/manifest.json")
def manifest():
    return send_from_directory(APP_DIR, "manifest.json")

def display_version():
    try:
        return os.path.getmtime(os.path.join(APP_DIR, "kef_display.html"))
    except OSError:
        return None

@app.route("/volume")
def get_volume():
    with cache_lock:
        data = dict(cache)
    data["display_version"] = display_version()
    return jsonify(data)

@app.route("/volume/set", methods=["POST"])
def set_volume():
    with cache_lock:
        if not cache["online"]:
            return jsonify({"ok": False, "error": "Speaker offline"}), 503
    try:
        data = request.get_json()
        vol = int(round(float(data.get("volume", 0))))
        vol = max(0, min(100, vol))
        tcp_call(speaker_ip, set_volume_cmd(vol))
        with cache_lock:
            cache.update({"volume": vol / 100.0, "online": True})
        save_state(vol / 100.0, cache["muted"])  # deliberate user action, worth persisting right away
        return jsonify({"ok": True, "volume": vol / 100.0})
    except Exception as e:
        with cache_lock:
            cache.update({"online": False, "error": str(e)})
        return jsonify({"ok": False, "error": str(e)}), 502

@app.route("/mute/set", methods=["POST"])
def set_mute():
    with cache_lock:
        if not cache["online"]:
            return jsonify({"ok": False, "error": "Speaker offline"}), 503
        vol = int(round(cache["volume"] * 100))
    try:
        data = request.get_json()
        muted = bool(data.get("muted", False))
        # Same SET command as volume - add 128 to the current volume byte to mute, plain
        # volume byte to unmute (protocol confirmed against aiokef's mute()/unmute()).
        tcp_call(speaker_ip, set_volume_cmd(vol + 128 if muted else vol))
        with cache_lock:
            cache.update({"muted": muted, "online": True})
        save_state(cache["volume"], muted)
        return jsonify({"ok": True, "muted": muted})
    except Exception as e:
        with cache_lock:
            cache.update({"online": False, "error": str(e)})
        return jsonify({"ok": False, "error": str(e)}), 502

@app.route("/status")
def get_status():
    return jsonify({"server": "ok", "speaker_ip": speaker_ip})

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", default=DEFAULT_SPEAKER_IP)
    parser.add_argument("--port", type=int, default=SERVER_PORT)
    args = parser.parse_args()
    speaker_ip = args.ip

    load_state()

    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()

    log.info(f"KEF volume server starting | speaker_ip={speaker_ip} | server_url=http://localhost:{args.port}")
    app.run(host="0.0.0.0", port=args.port, debug=False)
