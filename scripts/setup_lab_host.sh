#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Lab host bootstrap: verify x86_64 + docker, install containerlab, fetch the sonic-vs image.
# Idempotent — safe to re-run. Usage: scripts/setup_lab_host.sh [--force]
#
# Env overrides:
#   SONIC_VS_BRANCH        sonic-buildimage branch to pull            (default: 202411)
#   SONIC_VS_IMAGE_URL     full artifact URL, replaces the built one  (default: derived from branch)
#   CHAOSLAB_SONIC_IMAGE   image tag the topology file expects        (default: docker-sonic-vs:latest)
set -euo pipefail

BRANCH="${SONIC_VS_BRANCH:-202411}"
IMAGE="${CHAOSLAB_SONIC_IMAGE:-docker-sonic-vs:latest}"
# Latest successful build of the branch on the official vs pipeline (not on any docker registry).
URL="${SONIC_VS_IMAGE_URL:-https://sonic-build.azurewebsites.net/api/sonic/artifacts?branchName=${BRANCH}&platform=vs&target=target%2Fdocker-sonic-vs.gz}"
CACHE_DIR="${HOME}/.chaoslab/cache"
ARTIFACT="${CACHE_DIR}/docker-sonic-vs-${BRANCH}.gz"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

say()  { printf '\033[36m[bootstrap]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[bootstrap]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------- host checks
[[ "$(uname -s)" == "Linux" ]] || fail "the lab needs a Linux host (run it on an x86 VM; see topo/README.md)"
[[ "$(uname -m)" == "x86_64" ]] || fail "sonic-vs images are amd64-only; this host is $(uname -m)"

mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if (( mem_kb < 7000000 )); then
  say "WARNING: <7 GB RAM detected — two sonic-vs leafs want ~8 GB free (PRODUCT.md §7)"
fi

command -v docker >/dev/null 2>&1 \
  || fail "docker not found — install it first (e.g. 'sudo dnf install -y docker' on Amazon Linux)"
docker info >/dev/null 2>&1 \
  || fail "docker daemon unreachable — start it ('sudo systemctl start docker') and add $USER to the docker group"

# ---------------------------------------------------------------------- containerlab
if command -v containerlab >/dev/null 2>&1; then
  say "containerlab present: $(containerlab version 2>/dev/null | grep -m1 -io 'version:.*' || echo ok)"
else
  say "installing containerlab (official installer; uses sudo)..."
  bash -c "$(curl -sL https://get.containerlab.dev)" \
    || fail "containerlab install failed — see https://containerlab.dev/install/"
fi

# ---------------------------------------------------------------------- sonic-vs image
if docker image inspect "$IMAGE" >/dev/null 2>&1 && (( ! FORCE )); then
  say "image $IMAGE already present — nothing to do (--force re-downloads)"
else
  mkdir -p "$CACHE_DIR"
  if [[ ! -s "$ARTIFACT" || $FORCE -eq 1 ]]; then
    say "downloading docker-sonic-vs.gz (branch ${BRANCH}, latest successful build; ~1 GB)..."
    resolved=$(curl -fL --progress-bar -o "$ARTIFACT" -w '%{url_effective}' "$URL") \
      || fail "download failed — grab it manually (see topo/README.md) or set SONIC_VS_IMAGE_URL"
    say "resolved artifact: ${resolved}"    # branch artifacts rotate; keep this for traceability
  else
    say "using cached artifact ${ARTIFACT}"
  fi
  say "loading image into docker (takes a while)..."
  loaded=$(docker load -i "$ARTIFACT" | sed -n 's/^Loaded image: //p' | tail -n1)
  [[ -n "$loaded" ]] || fail "docker load produced no image — corrupt artifact? re-run with --force"
  say "loaded: ${loaded}"
  [[ "$loaded" == "$IMAGE" ]] || docker tag "$loaded" "$IMAGE"
  docker tag "$IMAGE" "docker-sonic-vs:${BRANCH}"
fi

docker image inspect "$IMAGE" >/dev/null 2>&1 || fail "image $IMAGE still missing after load"
say "done — next: 'make lab-up', then 'chaoslab settings set lab_mode local'"
