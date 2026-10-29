#!/usr/bin/env bash
# One-time setup of the edge device (sudo): Docker may pull from the registry on the build machine, and the demo's
# worker starts at boot once the microphone is there (run-demo.sh up worker).
set -euo pipefail
dir=$(cd "$(dirname "$0")" && pwd)

python3 - <<'PY'
import json, pathlib
p = pathlib.Path("/etc/docker/daemon.json")
c = json.loads(p.read_text()) if p.exists() else {}
registries = c.setdefault("insecure-registries", [])
if "192.168.1.99:5000" not in registries:
    registries.append("192.168.1.99:5000")
p.write_text(json.dumps(c, indent=2) + "\n")
PY
systemctl restart docker

cat > /etc/systemd/system/sigmod-demo.service <<UNIT
[Unit]
Description=SIGMOD demo, edge worker (docker compose)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
RequiresMountsFor=$dir

[Service]
Type=oneshot
RemainAfterExit=yes
User=pi
WorkingDirectory=$dir
ExecStart=$dir/run-demo.sh up worker
ExecStop=$dir/run-demo.sh stop
TimeoutStartSec=infinity
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable sigmod-demo
# It waits for the microphone, so do not block on it.
systemctl start --no-block sigmod-demo
