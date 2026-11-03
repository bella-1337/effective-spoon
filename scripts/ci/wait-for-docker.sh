#!/bin/sh
# Wait until the Docker daemon answers, or fail with its last error.
#
# GitLab Docker-in-Docker jobs call this before using Docker. A runner that is
# not configured for it (no `privileged = true`, or no shared `/certs/client`
# volume) then fails in seconds with the cause instead of hanging until the
# job timeout.
#
# Usage: scripts/ci/wait-for-docker.sh [timeout-seconds]   (default: 120)

set -eu

timeout=${1:-120}
case "$timeout" in
  '' | *[!0-9]*)
    echo "usage: $0 [timeout-seconds]" >&2
    exit 2
    ;;
esac

elapsed=0
until error=$(docker info 2>&1 >/dev/null); do
  if [ "$elapsed" -ge "$timeout" ]; then
    echo "Docker daemon at ${DOCKER_HOST:-the default socket} is not reachable after ${timeout}s:" >&2
    printf '%s\n' "$error" >&2
    echo "Docker-in-Docker jobs need a runner with privileged = true and" >&2
    echo "/certs/client in its volumes; see docs/development-workflow.md." >&2
    exit 1
  fi
  sleep 2
  elapsed=$((elapsed + 2))
done

echo "Docker daemon is ready: $(docker version --format 'server {{.Server.Version}}')"
