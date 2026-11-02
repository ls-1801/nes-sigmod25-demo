#!/bin/sh
# Starts this Pi's part of the demo (.env, rendered by demo-deploy: DEMO_SERVICES, empty for all services of the
# profiles). Run on boot by sigmod-demo.service. Images that are already here are not pulled again: demo-deploy pulls
# them, or run `./run-demo.sh pull` and then `sudo systemctl restart sigmod-demo`.
#
#   ./run-demo.sh            wait for the microphone, then start this Pi's services in the background
#   ./run-demo.sh up <args>  the same with more `docker compose up` options, e.g. `--pull always` for new images
#   ./run-demo.sh pull       fetch new images of this Pi's services
#   ./run-demo.sh <args>     any other docker compose command, e.g. `stop`, `ps`, `logs -f queries`
cd "$(dirname "$0")" || exit 1
# Per device settings (images, services, microphone) are in .env.
set -a; . ./.env; set +a
MICROPHONE=${MICROPHONE-K66} # ALSA card name, see /proc/asound/cards; empty on a device without the microphone
export NES_ALSA_DEVICE=plughw:CARD=$MICROPHONE,DEV=0 NES_AUDIO_GID=29
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
        exec docker compose up -d "$@" $DEMO_SERVICES ;;
    pull) exec docker compose pull $DEMO_SERVICES ;;
    *) exec docker compose "$@" ;;
esac
