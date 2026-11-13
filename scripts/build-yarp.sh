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
    # The pinned CTS tree (android-16.0.0_r1) does not list "13" in
    # cts/tests/tests/os/assets/platform_releases.txt, but our BoardConfig
    # pins PLATFORM_VERSION := 13 to match the stock NX729J boot header's
    # os_version field (0x1A00063C). tasks/cts.mk walks that directory
    # whenever the `cts/` folder exists and hard-errors on any version it
    # does not recognise. AOSP's own guidance for non-release platforms is
    # to append the version to those two asset files, so we do that before
    # kati reads them. This is a no-op when the version is already there.
    cts_assets="${source_root}/cts/tests/tests/os/assets"
    if [[ -d "${cts_assets}" ]]; then
      for cts_asset in platform_versions.txt platform_releases.txt; do
        cts_asset_path="${cts_assets}/${cts_asset}"
        [[ -f "${cts_asset_path}" ]] || continue
        if ! grep -qx "13" "${cts_asset_path}"; then
          printf '13\n' >> "${cts_asset_path}"
          echo "Appended 13 to ${cts_asset} for the PLATFORM_VERSION check."
        fi
      done
    fi
    # The recovery's on-device folder name is hardcoded in
    # bootable/recovery/variables.h (TW_RECOVERY_NAME, which feeds both
    # TW_DEFAULT_RECOVERY_FOLDER and TW_PERSIST_DIR), and nothing wires it
    # through soong_config. Patch the header to match the YARP branding
    # requested for this device tree.
    variables_h="${source_root}/bootable/recovery/variables.h"
    if [[ -f "${variables_h}" ]] && ! grep -q '^#define TW_RECOVERY_NAME  *"YARP"' "${variables_h}"; then
      sed -i 's|^#define TW_RECOVERY_NAME.*|#define TW_RECOVERY_NAME          "YARP"|' "${variables_h}"
      echo "Patched TW_RECOVERY_NAME -> YARP in ${variables_h#"$source_root"/}."
    fi
    # TWRP data.cpp hardcodes the default timezone as US Central
    # ("CST6CDT,M3.2.0,M11.1.0").  For this device tree the user asked for
    # China Standard Time (UTC+8, no DST).  Patch the default so the clock
    # reads correctly on first boot without user interaction.
    data_cpp="${source_root}/bootable/recovery/data.cpp"
    if [[ -f "${data_cpp}" ]] && grep -q '"CST6CDT,M3.2.0,M11.1.0"' "${data_cpp}"; then
      sed -i 's|"CST6CDT,M3.2.0,M11.1.0"|"CST-8"|' "${data_cpp}"
      echo "Patched TW_TIME_ZONE_VAR default -> CST-8 (Asia/Shanghai, no DST) in ${data_cpp#"$source_root"/}."
    fi
    mka -j"${BUILD_JOBS:-2}" installclean
    if ! mka -j"${BUILD_JOBS:-2}" recoveryimage; then
      echo "::error::mka recoveryimage failed." >&2
      # Soong product config is assembled from BoardConfig/device makefiles as
      # JSON.  A value that is not JSON safe (for example a quoted string) is
      # only reported by merge_json, so dump the neighbourhood of every such
      # file to make the offending line obvious in the build log.
      for f in "${source_root}"/out/soong/soong.*.extra.variables "${source_root}"/out/soong/soong.*.variables; do
        [[ -f "$f" ]] || continue
        echo "--- ${f}" >&2
        nl -ba "$f" | sed -n '1,80p' >&2
      done
      exit 1
    fi
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
    # Publish the image before verification so a strict static check can never
    # throw away a good build.
    cp "${product_root}/recovery.img" "${dist_root}/${image_name}"
    cd "${dist_root}"
    sha256sum "${image_name}" > SHA256SUMS.txt
    cat SHA256SUMS.txt
    python3 "${builder_root}/scripts/verify-image.py" "${product_root}" \
      > "${dist_root}/verification.json"
    cat verification.json
    ;;
  *)
    printf 'Usage: %s {sync|build|package}\n' "$0" >&2
    exit 2
    ;;
esac
