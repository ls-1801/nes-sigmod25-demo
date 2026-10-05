#!/usr/bin/env python3

from __future__ import annotations
import csv, os, time, threading, itertools, logging
from pathlib import Path
from datetime import datetime, timezone

import yaml
import paho.mqtt.client as mqtt

def utc_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1_000)

def read_csv_forever(path: Path):
    while True:
        with path.open(newline="") as f:
            rdr = csv.reader(f)
            # skip header
            first = next(rdr, None)
            if first and not is_number(first[0]):
                pass
            else:
                # first row is data; yield it
                if first:
                    yield first
            for row in rdr:
                yield row

def is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False

def make_mqtt_client(name: str) -> mqtt.Client:
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,
                       client_id=f"lab-pub-{name}")


def publisher(name: str, cfg: dict):
    log = logging.getLogger(name)
    csv_path = Path(f"{name}.csv")
    if not csv_path.is_file():
        log.error("CSV file %s not found – stream skipped.", csv_path)
        return

    period_s = float(cfg.get("period", 1)) # seconds
    broker   = cfg.get("broker", "localhost")
    port     = int(cfg.get("port", 1883))
    topic    = cfg.get("topic", f"lab/{name}")

    client = make_mqtt_client(name)

    def on_connect(client, userdata, flags, rc, *_):
        msg = "connected" if rc == 0 else f"connection failed (rc={rc})"
        log.info(msg)

    client.on_connect = on_connect
    client.connect(broker, port, keepalive=60)
    client.loop_start()

    try:
        for row in read_csv_forever(csv_path):
            payload = f"{utc_ms()*1000},{name},{','.join(row)}"
            client.publish(topic, payload, qos=1, retain=False)
            time.sleep(period_s)
    except Exception as e: # Log & exit – compose will restart
        log.exception("stream aborted: %s", e)
    finally:
        client.loop_stop()
        client.disconnect()

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S")

    cfg_file = Path("config.yml")
    if not cfg_file.is_file():
        raise SystemExit("config.yml not found")

    config: dict[str, dict] = yaml.safe_load(cfg_file.read_text()) or {}
    if not config:
        raise SystemExit("config.yml is empty")

    threads = []
    for name, section in config.items():
        t = threading.Thread(target=publisher, args=(name, section), daemon=True)
        t.start()
        threads.append(t)

    # keep the main thread alive so Docker sees a running process
    for t in threads:
        t.join()

if __name__ == "__main__":
    main()

