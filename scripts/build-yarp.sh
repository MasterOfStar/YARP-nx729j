#!/usr/bin/env bash
# Sync the pinned YARP (TWRP 16) sources, build the NX729J recovery image and
# package it with a static verification report.
#
#   scripts/build-yarp.sh sync|build|package
#
# The device tree is *not* a repo project: it lives in this repository under
# device/nubia/nx729j and is copied into the synced tree after `repo sync`.
# It must be a real directory: build/envsetup.sh discovers devices with `find`,
# which does not descend into symlinked directories.
set -euo pipefail

builder_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source_root="${YARP_SOURCE_DIR:-/build/yarp}"
dist_root="${YARP_DIST_DIR:-${builder_root}/dist}"
lock_file="${builder_root}/sources.json"
device_rel="device/nubia/nx729j"
product="nx729j"
image_name="YARP-nx729j-A13-recovery.img"
mkdir -p "${source_root}" "${dist_root}"

stage_device_tree() {
  mkdir -p "${source_root}/$(dirname "${device_rel}")"
  rm -rf "${source_root:?}/${device_rel}"
  mkdir -p "${source_root}/${device_rel}"
  tar -C "${builder_root}/${device_rel}" --exclude=.git -cf - . \
    | tar -C "${source_root}/${device_rel}" -xf -
  test -f "${source_root}/${device_rel}/BoardConfig.mk"
  test -f "${source_root}/${device_rel}/AndroidProducts.mk"
  echo "Staged ${builder_root}/${device_rel} -> ${source_root}/${device_rel} ($(du -sh "${source_root}/${device_rel}" | cut -f1))"
}

case "${1:-}" in
  sync)
    cd "${source_root}"
    manifest_revision="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["manifest"]["revision"])' "${lock_file}")"
    repo init -u https://github.com/TWRP-Test/platform_manifest_twrp_aosp.git \
      -b "${manifest_revision}" --depth=1 --no-clone-bundle --no-tags
    python3 "${builder_root}/scripts/source-lock.py" overlay "${lock_file}" "${source_root}"
    repo sync -c -j "${SYNC_JOBS:-8}" --fail-fast --no-tags --no-clone-bundle \
      --optimized-fetch --retry-fetches=3
    stage_device_tree
    python3 "${builder_root}/scripts/source-lock.py" verify "${lock_file}" "${source_root}"
    repo manifest -r -o "${dist_root}/source-manifest.xml"
    cp "${lock_file}" "${dist_root}/sources.json"
    git -C bootable/recovery diff --check
    df -h "${source_root}"
    du -sh .repo
    available_kib="$(df -Pk "${source_root}" | awk 'NR == 2 {print $4}')"
    if (( available_kib < 15 * 1024 * 1024 )); then
      echo '::error::Less than 15 GiB remains after source sync; refusing to fill the runner disk.'
      exit 1
    fi
    ;;
  build)
    cd "${source_root}"
    stage_device_tree
    export ALLOW_MISSING_DEPENDENCIES=true
    export USE_CCACHE=0
    export MALLOC_ARENA_MAX=2
    # Soong sanitizes its child environment. Use its explicit forwarding option.
    export SOONG_GOMEMLIMIT=8GiB
    export GOMEMLIMIT=8GiB
    export GOGC=50
    export GOMAXPROCS=2
    export SOONG_UI_NINJA_ARGS=-j1
    export NINJA_HIGHMEM_NUM_JOBS=1
    set +u
    source build/envsetup.sh
    lunch "twrp_${product}"
    if [[ "${TARGET_PRODUCT:-}" != "twrp_${product}" ]]; then
      echo "::error::lunch twrp_${product} did not select the product (TARGET_PRODUCT='${TARGET_PRODUCT:-}')." >&2
      exit 1
    fi
    echo "TARGET_PRODUCT=${TARGET_PRODUCT} TARGET_DEVICE=${TARGET_DEVICE:-}"
    mka -j"${BUILD_JOBS:-2}" installclean
    mka -j"${BUILD_JOBS:-2}" recoveryimage
    image="${source_root}/out/target/product/${product}/recovery.img"
    if [[ ! -s "${image}" ]]; then
      echo "::error::The build did not produce ${image}." >&2
      exit 1
    fi
    ls -l "${image}"
    ;;
  package)
    product_root="${source_root}/out/target/product/${product}"
    if [[ ! -s "${product_root}/recovery.img" ]]; then
      echo "::error::No recovery.img in ${product_root}." >&2
      exit 1
    fi
    python3 "${builder_root}/scripts/verify-image.py" "${product_root}" \
      > "${dist_root}/verification.json"
    cp "${product_root}/recovery.img" "${dist_root}/${image_name}"
    cd "${dist_root}"
    sha256sum "${image_name}" > SHA256SUMS.txt
    cat verification.json
    ;;
  *)
    printf 'Usage: %s {sync|build|package}\n' "$0" >&2
    exit 2
    ;;
esac
