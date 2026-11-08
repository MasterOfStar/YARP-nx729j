#!/usr/bin/env bash
set -euo pipefail

# This script deletes preinstalled software on a disposable GitHub VM only.
if [[ "${GITHUB_ACTIONS:-}" != true || "${RUNNER_ENVIRONMENT:-}" != github-hosted || "${RUNNER_OS:-}" != Linux ]]; then
  echo 'Refusing storage preparation outside a GitHub-hosted Linux runner.' >&2
  exit 1
fi

df -h / /mnt
free -h
sudo swapoff -a
sudo rm -f /mnt/swapfile /swapfile
sudo docker image prune --all --force
sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc \
  /usr/local/.ghcup /usr/local/swift /opt/hostedtoolcache
sudo apt-get clean

# Keep the existing filesystem: no loop images consuming the runner's root disk.
sudo fallocate -l 16G /yarp-swapfile
sudo chmod 600 /yarp-swapfile
sudo mkswap /yarp-swapfile
sudo swapon /yarp-swapfile
sudo mkdir -p /build/yarp
sudo chown "$(id -u):$(id -g)" /build/yarp
df -h / /build/yarp
free -h
swapon --show

available_kib="$(df -Pk /build/yarp | awk 'NR == 2 {print $4}')"
if (( available_kib < 95 * 1024 * 1024 )); then
  echo '::error::Less than 95 GiB is available for sources and outputs after swap allocation.'
  exit 1
fi
