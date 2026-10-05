import sys
import time
from datetime import datetime, timezone
import random
import paho.mqtt.client as mqtt
import yaml
from pathlib import Path


def load_cfg(path: str | Path = "config.yml") -> dict:
    p = Path(path)
    if p.is_file():
        with p.open() as f:
            return yaml.safe_load(f) or {}
    return {}


def on_connect(client, userdata, flags, rc, properties=None):
    print("[✓] Connected" if rc == 0 else f"[!] Connection failed (rc={rc})")


def interpolate(start, end, ratio):
    return round(start + (end - start) * ratio)


def run_generator(cfg: dict):
    BROKER_HOST = cfg.get("broker", "localhost")
    BROKER_PORT = cfg.get("port", 1883)
    TOPIC = cfg.get("topic", "patientMonitorReplay/Signal")
    PUBLISH_PERIOD = int(cfg.get("period", 0.3) * 1_000_000)
    START_EPOCH = int(cfg.get("start_epoch", 1735772400000000))
    CYCLE_DURATION = int(cfg.get("full_cycle_duration", 10) * 1_000_000)
    VALUES = cfg.get("values", [100, 60])
    INIT_PHASE = int(cfg.get("init_phase_duration", 3) * 1_000_000)
    TRANSITION_PHASE = int(cfg.get("phase_over_duration", 2) * 1_000_000)
    VARIATION = int(cfg.get("variation", 0))

    if len(VALUES) != 2:
        raise ValueError("`values` must contain exactly two numbers [normal, sick]")

    normal, sick = VALUES

    client = mqtt.Client()
    client.on_connect = on_connect
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()

    try:
        while True:
            # true microsecond-precision timestamp since epoch
            now_us = int(time.time() * 1_000_000)
            #now_us = int(time.time()) * 1_000_000
            #now_us = time.time_ns() // 1_000
            elapsed = (now_us - START_EPOCH) % CYCLE_DURATION
            msg_timestamp = START_EPOCH + elapsed*1000
            # use the timestamp below for live data ingestion
            timestamp = int(datetime.now(timezone.utc).timestamp() * 1_000_000)

            if elapsed < INIT_PHASE:
                value = normal
            elif elapsed < INIT_PHASE + TRANSITION_PHASE:
                ratio = (elapsed - INIT_PHASE) / TRANSITION_PHASE
                value = interpolate(normal, sick, ratio)
            else:
                value = sick

            if VARIATION > 0:
                value += random.randint(-VARIATION, VARIATION)
                value = max(0, value)

            msg = f"{timestamp},{value}"
            client.publish(TOPIC, msg, qos=1)

            next_tick = START_EPOCH + ((now_us - START_EPOCH) // PUBLISH_PERIOD + 1) * PUBLISH_PERIOD
            #sleep_time = max(0, (next_tick - int(time.time() * 1_000_000)) / 1_000_000)
            now_us2 = time.time_ns() // 1_000
            #now_us2 = int(time.time()) * 1_000_000
            sleep_time = max(0, (next_tick - now_us2) / 1_000_000)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\n[×] Stopping generator...")
    finally:
        client.loop_stop()
        client.disconnect()


def main():
    section = ""
    for arg in sys.argv[1:]:
        if arg.startswith("CONF="):
            section = arg.split("=", 1)[1]
    if not section:
        raise Exception("No config section specified")

    cfg = load_cfg().get(section, {})
    run_generator(cfg)


if __name__ == "__main__":
    main()
