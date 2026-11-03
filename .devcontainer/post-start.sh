#!/usr/bin/env bash
# Keep long-running `docker run` output intact through the Docker socket relay.
#
# On Docker Desktop the docker-outside-of-docker feature cannot match the host
# socket's group, so it relays /var/run/docker-host.sock to /var/run/docker.sock
# with socat. socat closes a connection 0.5 s after one side half-closes it, and
# `docker run` half-closes as soon as it has no stdin. Output a container prints
# after a pause is then lost (exit codes are unaffected). Restart the relay with
# a one-hour half-close timeout. Linux hosts without the relay are unchanged.

set -euo pipefail

readonly HOST_SOCKET=/var/run/docker-host.sock
readonly SOCKET=/var/run/docker.sock
readonly PID_FILE=/tmp/vscr-docker-from-docker.pid
readonly HALF_CLOSE_TIMEOUT_SECONDS=3600

relay_pids() {
  pgrep -f "^socat .*UNIX-LISTEN:${SOCKET},.*UNIX-CONNECT:${HOST_SOCKET}" || true
}

# The feature starts the relay in the background; give it a moment.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [[ -n "$(relay_pids)" ]] && break
  sleep 1
done

pids=$(relay_pids)
if [[ -z "$pids" ]]; then
  exit 0
fi
if pgrep -f "^socat -t ${HALF_CLOSE_TIMEOUT_SECONDS} .*UNIX-LISTEN:${SOCKET}," >/dev/null; then
  exit 0
fi

user=$(id -un)
# shellcheck disable=SC2086 # one PID per word
sudo kill $pids
sudo rm -f -- "$SOCKET"
sudo sh -c '
  socat -t "$1" "UNIX-LISTEN:$2,fork,mode=660,user=$3,backlog=128" "UNIX-CONNECT:$4" \
    >/dev/null 2>&1 &
  echo "$!" > "$5"
' _ "$HALF_CLOSE_TIMEOUT_SECONDS" "$SOCKET" "$user" "$HOST_SOCKET" "$PID_FILE"

for _ in 1 2 3 4 5 6 7 8 9 10; do
  [[ -S "$SOCKET" ]] && break
  sleep 0.5
done
if ! docker version >/dev/null 2>&1; then
  echo "Docker socket relay restart failed; reopen the container." >&2
  exit 1
fi
echo "Docker socket relay keeps half-closed streams for ${HALF_CLOSE_TIMEOUT_SECONDS}s."
