# Deploying the demo to two Raspberry Pis

The edge Pi runs a worker with the sources (camera, microphone, sensors, system metrics) and most operators; the hub Pi
runs a worker with the MQTT sinks, the broker, the web UI on its screen (kiosk), the replayed sensors and the queries
service. [`inventory.yml`](inventory.yml) names both Pis, their demo directories, the Pi with the microphone, and the
registry and image tag.

## Fresh Pis

1. Raspberry Pi OS (trixie, with desktop) on both, user `pi`, SSH key of this machine in `~pi/.ssh/authorized_keys`.
2. Network configured: both Pis reach each other, the camera, the registry and (while deploying) the internet.
3. Enter the Pis' addresses and directories in `inventory.yml`.
4. `ansible-playbook site.yml -K` (from this directory; `-K` asks for the sudo password, leave it out where `pi` has
   passwordless sudo).

The playbook installs Docker, points it at the registry, syncs the clocks to `ntp_server`, copies the demo files,
renders `.env` per Pi and `topology.yaml` on the hub, installs and enables `sigmod-demo.service` on both and the kiosk
session on the hub, pulls the images, and starts the edge, then the hub. From then on the Pis start the demo on boot.
The edge's worker starts only once the microphone is plugged in.

The first start compiles the Codon UDFs (a few minutes) and installs the UDF libraries into `.venv` (needs the internet).

## Updating

* New images: build them, push with `./publish-images.sh <nebulastream checkout> <nebulastream-ui-SIGMOD-Demo checkout>`
  (the tag is `demo_tag` of `inventory.yml`), then `ansible-playbook site.yml`.
* Changed queries, UDFs, compose or scripts: `ansible-playbook site.yml`.

The registry on the build machine:
`docker run -d --restart=always --name registry -p 5000:5000 -v registry-data:/var/lib/registry registry:2`
