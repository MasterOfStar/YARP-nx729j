#!/usr/bin/env bash
set -uo pipefail

while true; do
  date -u '+%Y-%m-%dT%H:%M:%SZ'
  free -m
  df -h / "${YARP_SOURCE_DIR:-/build/yarp}"
  ps -eo pid,comm,rss --sort=-rss | head -n 9 || true
  if [[ -d /sys/fs/cgroup/yarp-build.scope ]]; then
    for metric in memory.current memory.peak memory.swap.current memory.events; do
      echo "${metric}:"
      cat "/sys/fs/cgroup/yarp-build.scope/${metric}" 2>/dev/null || true
    done
  fi
  available_kib="$(df -Pk / | awk 'NR == 2 {print $4}')"
  if (( available_kib < 3 * 1024 * 1024 )); then
    echo '::error::Stopping the build to preserve 3 GiB for the runner and diagnostic uploads.'
    sudo systemctl stop yarp-build.scope
    exit 1
  fi
  sleep 60
done
