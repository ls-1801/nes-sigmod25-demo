# NebulaStream SIGMOD '25 demo

Camera, microphone and replayed patient-monitor / lab-value streams processed by NebulaStream and shown in the SIGMOD UI.
The queries are in [`topology.yaml`](topology.yaml), the Python UDFs (compiled by Codon inside the worker) in
[`python_udfs/`](python_udfs/), the models in [`models/`](models/).

```
git clone https://github.com/ls-1801/nes-sigmod25-demo && cd nes-sigmod25-demo
NES_DOCKER_TAG=<tag> NES_ALSA_DEVICE=plughw:1,0 docker compose up
```

then open <http://localhost:3000>. Ctrl-C (or `docker compose stop`) stops the queries first, then the worker; `docker compose down`
removes the containers.

| service | |
|---|---|
| `worker` | `nes-worker`, with the camera (host network) and the microphone (`/dev/snd`) |
| `queries` | `nes-cli` running `queries.sh`: waits for the worker, starts all queries of `topology.yaml`, reports queries that fail (`QUERY <name> is Failed: <error>`), stops them on shutdown |
| `mqtt` | Mosquitto, MQTT on 1883 and WebSocket on 9001 |
| `webui` | the SIGMOD UI on port 3000 |
| `patient-monitor`, `lab-values` | replayed sensors publishing to `source/live/*` |

## Images

Nothing is built locally. Compose pulls `${NES_IMAGE_REPO:-nebulastream}/<name>:${NES_DOCKER_TAG:-local}` for `nes-worker`,
`nes-cli`, `sigmod-ui`, `sigmod-patient-monitor` and `sigmod-lab-values`. Publish them for the architecture the demo machine
runs (arm64 for a Raspberry Pi) under the repository and tag you pass. The worker and CLI images come from the NebulaStream
build (`cmake --build <dir> --target package-docker-nes-worker package-docker-nes-cli`, with `NES_DOCKER_TAG`), the UI image from
the SIGMOD UI repository, the two generator images from [`generators/`](generators/):
`docker compose -f compose.yaml -f compose.build.yaml build` builds those locally.

## Variables

* `NES_DOCKER_TAG`, `NES_IMAGE_REPO`: image tag and repository prefix.
* `NES_ALSA_DEVICE`: ALSA capture device inside the worker, default `plughw:0,0`. `arecord -l` on the host lists the cards; the
  container only sees the host's cards, so it is usually `plughw:<card>,0`. `NES_AUDIO_GID`: the host's `audio` group id if it
  differs from the container's.
* `NES_WORKER_MEMORY_BYTES`: worker memory, default 8 GiB (the video path was tested with 32 GiB).
* `NES_DEMO_FAIL_FAST=1`: stop everything as soon as one query fails. `NES_DEMO_TOPOLOGY`: another topology file.

## Requirements

* **Rootful Docker with host networking.** The camera (GigE Vision) is reached through the host's network interface. With rootless
  Docker, "host" networking is RootlessKit's namespace, which has none of the host's interfaces.
* **The camera's route.** If several network interfaces share the camera's subnet, give the camera's interface its own route
  (`nmcli con mod <connection> +ipv4.routes "<camera ip>/32"`). The worker runs without `CAP_NET_RAW`, so Aravis receives the stream
  through a normal UDP socket that follows the routing table (with the capability it opens a raw packet socket on the interface
  it finds by the host's IP address, which is the wrong one when addresses are shared).
* Free ports: 1883, 9001, 3000, 8080 and 9090.
* The repository is bind-mounted into the worker and the `queries` container: the topology, the models and the UDFs are found by
  relative paths. Run compose from the checkout.

## What the demo shows

| topic (UI) | produced by |
|---|---|
| `test/thermal-rgb-image` | `thermal_face`: face detection (YuNet, through `MODEL_INFERENCE`) on the RGB frame; thermal frame with the face outlined and `max_temp` when there is a face, otherwise the RGB frame |
| `sink/live/alert`, `alertType` 5 | `shock_alert`: a face above 34 °C while heart rate / systolic pressure is above 1 (the replayed vitals reach that in the second half of their 96 s cycle) |
| `sink/live/alert`, `alertType` 9 | `speech_alert`: keyword model, class 9 ("go") heard clearly |
| `sink/live/audio` | the microphone waveform |
| `sink/live/patient_monitor_*`, `sink/live/lab_values` | the replayed sensor streams, republished by the pass-through queries |

The ASOF joins pair every thermal frame with the latest RGB frame, and every heart-rate value with the latest blood pressure.
