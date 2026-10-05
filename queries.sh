#!/usr/bin/env bash
# Starts all queries of the topology once the worker is healthy, watches them, and stops them again when it is
# terminated. Runs as the `queries` service of compose.yaml, inside the nes-cli image, with the repository as working
# directory.
#
# A query that is not running is reported once ("QUERY <name> is Failed: <error>") and the others keep running. The script
# exits with a non-zero status when a query failed or the worker went away, so that the container is restarted, which
# starts everything again. SIGTERM or SIGINT (docker compose stop/down, Ctrl-C) stop all queries first.
#
# Environment:
#   NES_CLI                   nes-cli binary (default nes-cli)
#   NES_DEMO_TOPOLOGY         topology file (default topology.yaml)
#   NES_WORKER_ADDRESS        gRPC address of the worker (default localhost:8080)
#   NES_DEMO_POLL_INTERVAL    seconds between status polls (default 5)
#   NES_DEMO_HEALTH_TIMEOUT   seconds to wait for the worker (default 300)
#   NES_DEMO_FAIL_FAST        1: stop everything as soon as one query fails
set -uo pipefail

nes_cli=${NES_CLI:-nes-cli}
topology=${NES_DEMO_TOPOLOGY:-topology.yaml}
worker=${NES_WORKER_ADDRESS:-localhost:8080}
poll_interval=${NES_DEMO_POLL_INTERVAL:-5}
health_timeout=${NES_DEMO_HEALTH_TIMEOUT:-300}

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
cli() { "$nes_cli" -t "$topology" "$@"; }
# A plain sleep would delay the signal handlers until it ends.
nap() { sleep "$1" & wait $!; }

worker_healthy() { /bin/grpc_health_probe -addr="$worker" -connect-timeout 2s >/dev/null 2>&1; }

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

queries_started=0
failures=0
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
    else
        log "stopping the queries failed (is the worker still running?)"
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

declare -A reported=()
last_summary=$SECONDS
while true; do
    nap "$poll_interval"

    if ! worker_healthy; then
        log "the worker is gone, ending (the container restarts and starts everything again)"
        failures=$((failures + 1))
        break
    fi
    if ! states=$(query_states); then
        log "could not read the query status"
        continue
    fi

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
