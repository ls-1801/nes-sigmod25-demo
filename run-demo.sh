#!/bin/sh
# Starts the whole demo on this machine (worker, queries, generators, broker, web UI on http://localhost:3000).
# Run on boot by sigmod-demo.service. Images that are already here are not pulled again: after a new build on the
# build machine run `./run-demo.sh pull` and then `sudo systemctl restart sigmod-demo`.
#
#   ./run-demo.sh            wait for the microphone, then start everything in the background
#   ./run-demo.sh up <args>  the same with more `docker compose up` options, e.g. `--pull always` for new images
#   ./run-demo.sh pull       fetch new worker and CLI images
#   ./run-demo.sh <args>     any other docker compose command, e.g. `stop`, `ps`, `logs -f queries`
cd "$(dirname "$0")" || exit 1
# Per device settings (topology, microphone) are in .env.
set -a; . ./.env; set +a
MICROPHONE=${MICROPHONE-K66} # ALSA card name, see /proc/asound/cards; empty on a device without the microphone
export NES_DOCKER_TAG=demo-rebase-10-29-2026 NES_ALSA_DEVICE=plughw:CARD=$MICROPHONE,DEV=0 NES_AUDIO_GID=29
export COMPOSE_FILE=compose.yaml:compose.local.yaml
case ${1:-up} in
    up)
        [ $# -gt 0 ] && shift
        # The audio queries fail for good when the card is missing while they start, so wait for it (no time limit).
        until [ -z "$MICROPHONE" ] || grep -q "\[$MICROPHONE *\]" /proc/asound/cards; do
            [ -n "$waiting" ] || echo "waiting for the microphone (ALSA card $MICROPHONE), plug it in"
            waiting=1
            sleep 2
        done
        exec docker compose up -d "$@" ;;
    pull) exec docker compose pull worker queries ;;
    *) exec docker compose "$@" ;;
esac
