# Setup

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

## Deploying updates

The server runs this repo as a git clone (pulled read-only via a deploy key scoped to just this repo), so the repo is the source of truth rather than manual file copying. To ship a change:

```bash
git commit -am "..." && git push
ssh <user>@<server-ip> "cd /path/to/kef-volume-monitor && git pull"
# restart the service only if kef_server.py changed:
ssh <user>@<server-ip> "sudo systemctl restart kef-server.service"
```
