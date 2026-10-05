from medical_waveforms.synthetic import synthetic_arterial_pressure_data
from medical_waveforms.waveforms import Waveforms
from medical_waveforms.features.waveform import find_troughs
from medical_waveforms.features import cycles
from medical_waveforms import quality
import numpy as np
import time
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
import yaml
from pathlib import Path

def load_cfg(path: str | Path = "config.yml") -> dict:
  p = Path(path)
  if p.is_file():
    with p.open() as f:
      return yaml.safe_load(f) or {}
  return {}

def generate_data():
  fps = 50
  heart_rate = 60
  sample_rate = 200
  x_window = 3 # seconds
  slide_duration = 10 # seconds
  samples_in_window = int(sample_rate * x_window)
  # generate data
  data = synthetic_arterial_pressure_data(
    systolic_pressure=80,
    diastolic_pressure=65,
    heart_rate=heart_rate,
    n_beats_target=5.5,
    hertz=sample_rate
  )
  tile = data.pressure[140:sample_rate+140]
  tile_count = int(x_window * sample_rate) + (slide_duration * sample_rate)
  data = np.tile(tile,tile_count)
  print(np.shape(data))
  return data

def on_connect(client, userdata, flags, rc, properties=None):
  print("[✓] Connected" if rc == 0 else f"[!] Connection failed (rc={rc})")

def on_publish(client, userdata, mid):
  print(f"CSV message {mid} published")

def main() -> None:

  cfg = yaml.safe_load(Path("config.yml").read_text()) or {}
  abp_cfg = cfg.get("abp", {})

  BROKER_HOST    = abp_cfg.get("broker", "localhost")
  BROKER_PORT    = abp_cfg.get("port", 1882)
  TOPIC          = abp_cfg.get("topic", "patientMonitor/ABP")
  PUBLISH_PERIOD = abp_cfg.get("period", 30) # in ms

  data = generate_data()
  index = 0

  client = mqtt.Client()
  client.on_connect = on_connect
  #client.on_publish = on_publish

  client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
  client.loop_start()

  try:
    while True:
      timestamp = int(datetime.now(timezone.utc).timestamp() * 1_000)

      csv_row = f"{timestamp},{data[index]}"

      client.publish(TOPIC, csv_row, qos=1, retain=False)

      index = (index + 1) % len(data)

      time.sleep(PUBLISH_PERIOD/1000)

  except KeyboardInterrupt:
    print("\n[×] Stopping publisher...")
  finally:
    client.loop_stop()
    client.disconnect()

if __name__ == "__main__":
  main()