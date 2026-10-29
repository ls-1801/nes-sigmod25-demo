#!/usr/bin/env bash
# Starts all queries of the topology once the worker is healthy, watches them, and stops them again when it is
# terminated. Runs as the `queries` service of compose.yaml, inside the nes-cli image, with the repository as working
# directory.
#
# A query that is not running is reported once ("QUERY <name> is Failed: <error>") and the others keep running. The script
# exits with a non-zero status when a query failed or the worker went away, so that the container is restarted, which
# starts everything again. SIGTERM or SIGINT (docker compose stop/down, Ctrl-C) stop all queries first.
#
# After every status poll the state of each query goes to the broker as one retained JSON message on
# NES_DEMO_STATUS_TOPIC, for the web UI: {"timestamp": <epoch ms>, "queries": [{"name", "status", "error"}, ...],
# "versions": {"cli": {...}, "worker": {...}}}, the versions as `--version` prints them ({"version", "commit", "built",
# ...}); the worker's comes from .nes-worker-version, written by the udf-venv service of compose.yaml.
#
# Environment:
#   NES_CLI                   nes-cli binary (default nes-cli)
#   NES_DEMO_TOPOLOGY         topology file (default topology.yaml)
#   NES_WORKER_ADDRESS        gRPC address of the worker (default localhost:8080)
#   NES_DEMO_MQTT_ADDRESS     host:port of the MQTT broker the queries use (default mqtt:1883), empty: do not wait for it
#   NES_ALSA_DEVICE           the worker's capture device; with a card in it (plughw:CARD=K66,DEV=0 or hw:2,0) the
#                             script waits for that card before starting the queries, and starts them again when the
#                             card shows up after missing (late USB enumeration, a reset), as the audio source fails
#                             for good without it
#   NES_DEMO_STATUS_TOPIC     topic of the query states (default demo/queries)
#   NES_DEMO_ASOUND_CARDS     the host's /proc/asound/cards (default /run/host-asound/cards, mounted by compose.yaml)
#   NES_DEMO_POLL_INTERVAL    seconds between status polls (default 60)
#   NES_DEMO_HEALTH_TIMEOUT   seconds to wait for the worker, and as long again for the broker and the microphone
#                             (default 300)
#   NES_DEMO_FAIL_FAST        1: stop everything as soon as one query fails
set -uo pipefail

nes_cli=${NES_CLI:-nes-cli}
topology=${NES_DEMO_TOPOLOGY:-topology.yaml}
worker=${NES_WORKER_ADDRESS:-localhost:8080}
mqtt=${NES_DEMO_MQTT_ADDRESS-mqtt:1883}
status_topic=${NES_DEMO_STATUS_TOPIC:-demo/queries}
asound_cards=${NES_DEMO_ASOUND_CARDS:-/run/host-asound/cards}
# The card of the capture device: its name (CARD=K66) or its number (hw:2,0); empty for other device names.
alsa_device=${NES_ALSA_DEVICE:-}
case $alsa_device in
    *CARD=*) alsa_card=${alsa_device#*CARD=}; alsa_card=${alsa_card%%,*} ;;
    *hw:[0-9]*) alsa_card=${alsa_device#*hw:}; alsa_card=${alsa_card%%,*} ;;
    *) alsa_card= ;;
esac
poll_interval=${NES_DEMO_POLL_INTERVAL:-60}
health_timeout=${NES_DEMO_HEALTH_TIMEOUT:-300}

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
cli() { "$nes_cli" -t "$topology" "$@"; }
# A plain sleep would delay the signal handlers until it ends.
nap() { sleep "$1" & wait $!; }

worker_healthy() { /bin/grpc_health_probe -addr="$worker" -connect-timeout 2s >/dev/null 2>&1; }
# The broker accepts TCP connections (bash's /dev/tcp, in a child so that the timeout can end it).
broker_up() { timeout 2 bash -c ": </dev/tcp/${mqtt%:*}/${mqtt##*:}" 2>/dev/null; }
# The card is listed as " 2 [K66            ]: USB-Audio - K66".
card_present() { grep -Eq "^ *($alsa_card \\[|[0-9]+ \\[$alsa_card +\\])" "$asound_cards"; }
card_watched() { [ -n "$alsa_card" ] && [ -r "$asound_cards" ]; }
# Set when the card was missing at some point since the queries started.
card_missed=0
# nap that checks for the card every 2 seconds, so that a short USB reset is noticed too.
watch_nap() {
    local waited=0
    while [ "$waited" -lt "$1" ]; do
        nap 2
        waited=$((waited + 2))
        if card_watched && [ "$card_missed" = 0 ] && ! card_present; then
            card_missed=1
            log "the sound card $alsa_card is gone, the audio queries fail until it is back"
        fi
    done
}

# Prints "<query> <status> <error>" for every query of the topology, nothing when the status cannot be read.
query_states() {
    local out
    out=$(cli status 2>&1) || { printf '%s\n' "$out" >&2; return 1; }
    printf '%s' "$out" | python3 -c '
import json, sys
for entry in json.load(sys.stdin):
    if "worker" in entry:  # per-worker entry; the entry without one is the aggregate of the query
        continue
    error = (entry.get("error") or "").replace("\n", " ")
    print(entry["query_id"], entry["query_status"], error[:400])
'
}

# Publishes the query_states lines on stdin as the retained message on $status_topic; with an argument, that is the
# status of every query (the error is dropped). A plain MQTT 3.1.1 client, as the nes-cli image has none.
publish_states() {
    [ -n "$mqtt" ] || return 0
    NES_CLI_VERSION=$("$nes_cli" --version 2>&1) NES_WORKER_VERSION=$(cat .nes-worker-version 2>/dev/null) python3 -c '
import json, os, socket, sys, time
host, port = sys.argv[1].rsplit(":", 1)
override = sys.argv[3] if len(sys.argv) > 3 else ""
queries = []
for line in sys.stdin:
    name, status, *error = line.rstrip("\n").split(" ", 2)
    if name:
        queries.append({"name": name, "status": override or status, "error": "" if override or not error else error[0]})

def version(text):
    # "nes-cli TBA" and then "  commit:  17b2bd73..." lines
    lines = text.strip().splitlines()
    if not lines:
        return None
    fields = {"version": lines[0].split(" ", 1)[-1]}
    for line in lines[1:]:
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields

versions = {"cli": version(os.environ["NES_CLI_VERSION"]), "worker": version(os.environ["NES_WORKER_VERSION"])}
payload = json.dumps({"timestamp": int(time.time() * 1000), "queries": queries, "versions": versions}).encode()

def packet(kind, body):
    n, length = len(body), b""
    while True:
        n, digit = divmod(n, 128)
        length += bytes([digit | (128 if n else 0)])
        if not n:
            return bytes([kind]) + length + body

def string(value):
    return len(value).to_bytes(2, "big") + value

with socket.create_connection((host, int(port)), timeout=5) as connection:
    # CONNECT with a clean session and a 30 s keep-alive, then wait for the CONNACK
    connection.sendall(packet(0x10, string(b"MQTT") + bytes([4, 2]) + (30).to_bytes(2, "big") + string(b"nes-demo-queries")))
    if connection.recv(4)[3:4] != b"\0":
        sys.exit("the broker refused the connection")
    # PUBLISH with QoS 0 and retain, then DISCONNECT
    connection.sendall(packet(0x31, string(sys.argv[2].encode()) + payload) + bytes([0xE0, 0]))
' "$mqtt" "$status_topic" "$@" || log "publishing the query states on $status_topic failed"
}

queries_started=0
failures=0
# The last query_states output, published as stopped when the queries end.
last_states=''

cleaned=0
cleanup() {
    [ "$cleaned" = 1 ] && return
    cleaned=1
    # Not `trap ''`: an ignored signal stays ignored in the children, and the converter that nes-cli runs for the model
    # (ovc) could then no longer end its subprocess with SIGTERM, which blocks every nes-cli call.
    trap 'log "already stopping the queries"' INT TERM
    [ "$queries_started" = 1 ] || return
    log "stopping the queries"
    if cli stop >/dev/null 2>&1; then
        # Stopping is asynchronous: wait until no query runs any more.
        for _ in $(seq 1 30); do
            running=$(query_states 2>/dev/null | awk '$2 == "Running"' | wc -l)
            [ "$running" -eq 0 ] && break
            nap 1
        done
        log "queries stopped"
        printf '%s\n' "$last_states" | publish_states Stopped
    else
        log "stopping the queries failed (is the worker still running?)"
        printf '%s\n' "$last_states" | publish_states Unknown
    fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

log "waiting for the worker at $worker"
deadline=$((SECONDS + health_timeout))
until worker_healthy; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        log "the worker did not become healthy within ${health_timeout}s"
        exit 1
    fi
    nap 2
done
log "the worker is healthy"

# Queries whose MQTT source or sink cannot connect fail for good, so the broker must be up before they start. compose
# does not order the broker before this container (it is not there with --profile headless, and the Docker daemon
# restarts containers at boot without regard to depends_on).
if [ -n "$mqtt" ]; then
    log "waiting for the MQTT broker at $mqtt"
    deadline=$((SECONDS + health_timeout))
    until broker_up; do
        if [ "$SECONDS" -ge "$deadline" ]; then
            log "the MQTT broker did not accept connections within ${health_timeout}s"
            exit 1
        fi
        nap 2
    done
    log "the MQTT broker is up"
fi

# The audio source fails for good when the card is missing while it starts (a USB microphone can show up minutes
# after boot). The other queries do not need it: after the timeout they start anyway, without audio.
if card_watched; then
    log "waiting for the sound card $alsa_card ($alsa_device)"
    deadline=$((SECONDS + health_timeout))
    until card_present; do
        if [ "$SECONDS" -ge "$deadline" ]; then
            log "the sound card $alsa_card did not show up within ${health_timeout}s, starting without it (the audio queries will fail)"
            break
        fi
        nap 2
    done
    if card_present; then log "the sound card $alsa_card is there"; else card_missed=1; fi
fi

# Queries of an earlier run (this container restarted) may still run on the worker: begin from a clean slate.
cli stop >/dev/null 2>&1 && nap 3

log "starting the queries of $topology"
queries_started=1  # also on a partial start, so that cleanup stops what did start
if ! started=$(cli start 2>&1); then
    log "starting the queries failed:"
    printf '%s\n' "$started" >&2
    exit 1
fi
total=$(printf '%s\n' "$started" | grep -c .)
log "started $total queries"
last_states=$(query_states) && printf '%s\n' "$last_states" | publish_states

declare -A reported=()
last_summary=$SECONDS
while true; do
    watch_nap "$poll_interval"

    if [ "$card_missed" = 1 ] && card_present; then
        log "the sound card $alsa_card is there again, ending (the container restarts and starts everything again)"
        failures=$((failures + 1))
        break
    fi
    if ! worker_healthy; then
        log "the worker is gone, ending (the container restarts and starts everything again)"
        failures=$((failures + 1))
        break
    fi
    if ! states=$(query_states); then
        log "could not read the query status"
        continue
    fi
    last_states=$states
    printf '%s\n' "$states" | publish_states

    running=0
    while read -r id status error; do
        [ -n "$id" ] || continue
        if [ "$status" = Running ]; then
            running=$((running + 1))
            if [ -n "${reported[$id]:-}" ]; then
                log "query $id is running again"
                unset 'reported[$id]'
            fi
            continue
        fi
        if [ "${reported[$id]:-}" != "$status" ]; then
            reported[$id]=$status
            failures=$((failures + 1))
            log "QUERY $id is $status${error:+: $error}"
        fi
    done <<<"$states"

    if [ "${NES_DEMO_FAIL_FAST:-0}" = 1 ] && [ "${#reported[@]}" -gt 0 ]; then
        log "a query failed, ending (NES_DEMO_FAIL_FAST)"
        break
    fi
    if [ "$running" -eq 0 ]; then
        log "no query is running any more, ending"
        break
    fi
    if [ $((SECONDS - last_summary)) -ge 60 ]; then
        last_summary=$SECONDS
        log "$running/$total queries running"
    fi
done

[ "$failures" -eq 0 ]
