# YARP recovery for the Nubia RedMagic 8 Pro (NX729J) — with `/data` decryption

This repository builds a **YARP (TWRP 16 / GUI2) recovery** for the Nubia
RedMagic 8 Pro, model **NX729J** (Qualcomm SM8550 *kalama*, Android 13,
`qssi-user 13 TKQ1.220915.002`), with **FBE + metadata-encryption decryption
enabled** — the capability the existing public NX729J device trees leave
unimplemented.

Everything needed is in this repository: the device tree, the stock vendor
crypto blobs (with a documented provenance and dependency closure), the pinned
TWRP 16 source lock, and a GitHub Actions workflow that produces
`recovery.img`.

```
.
├── device/nubia/nx729j/     # device tree (BoardConfig, fstab, init, blobs)
├── scripts/
│   ├── build-yarp.sh        # sync | build | package
│   ├── check-tree.py        # static tree validation (no AOSP needed)
│   ├── verify-image.py      # post-build image + decryption artifact checks
│   ├── fetch-blobs.py       # reproduces the staged vendor blob set
│   ├── source-lock.py       # repo overlay / pin verification
│   ├── prepare-runner.sh    # runner storage setup
│   └── resource-report.sh   # memory/disk watchdog during the build
├── provenance/              # where every staged vendor blob came from
├── sources.json             # pinned TWRP 16 manifest + projects
└── .github/workflows/build.yml
```

## Status

| Item | State |
| --- | --- |
| Device tree | complete, statically validated (`scripts/check-tree.py`) |
| Decryption stack | FBE v2 + `wrappedkey_v0` + metadata encryption wired end to end |
| Image build | **not run here** — a full sync needs ~95 GiB and the build ~15 GiB; run the workflow |
| On-device boot / decryption | **untested** — needs a device |

`scripts/check-tree.py` runs in seconds and covers the build config, the
recovery fstab, the ELF architecture and `DT_NEEDED` closure of every staged
vendor object, the init scripts and the VINTF manifest. The workflow runs it
*before* syncing sources, so a broken tree fails fast.

## Building

The build needs a runner with **≥ 95 GiB free** (source sync) plus ~15 GiB of
build output, 4+ cores and 16 GiB RAM; it cannot be produced inside a small
container.

1. Push this repository to GitHub (or fork it).
2. *Actions → Build YARP recovery for Nubia NX729J → Run workflow*.
3. Download the `YARP-nx729j-<run_id>` artifact; it contains
   `YARP-nx729j-A13-recovery.img`, `verification.json` and `SHA256SUMS.txt`.

The workflow pins every source to a commit (`sources.json`), builds with
`lunch twrp_nx729j` + `mka recoveryimage`, and enforces a 100 MiB image limit.

To do the same on your own machine:

```bash
export YARP_SOURCE_DIR=/build/yarp     # needs ~95 GiB free
python3 scripts/fetch-stock-kernel.py  # staged kernel for the build's VINTF checks
python3 scripts/check-tree.py          # static sanity checks, a few seconds
scripts/build-yarp.sh sync
scripts/build-yarp.sh build            # ~2-4 h on 4 cores
scripts/build-yarp.sh package
```

`scripts/build-yarp.sh` copies `device/nubia/nx729j` into the synced tree (a real
directory: `build/envsetup.sh` finds devices with `find`, which skips symlinks), so
this device tree is never fetched from a remote and is always the one you have
checked out.

## Download

A prebuilt image is attached to the
[v1.0-nx729j release](https://github.com/MasterOfStar/YARP-nx729j/releases/tag/v1.0-nx729j):

```
https://github.com/MasterOfStar/YARP-nx729j/releases/download/v1.0-nx729j/YARP-nx729j-A13-recovery.img
sha256 085a8a10fd3367b42fcdc01ed9d8bb67d6989e0444a90081ce05f110b5a252db
```

Build run: https://github.com/MasterOfStar/YARP-nx729j/actions/runs/42506856226 (all steps green).

## Flashing

The device has an independent `recovery` partition in slot `a`/`b`
(`ro.build.ab_update=true`, and the stock `vendor/etc/fstab.qcom` mounts
`/dev/block/by-name/recovery`):

```bash
fastboot flash recovery_ab YARP-nx729j-A13-recovery.img
fastboot reboot recovery      # or: hold Vol+ + Power
```

Keep a copy of your stock `recovery_a`/`recovery_b` before flashing (TWRP itself
can back it up once booted, or use `fastboot fetch`/`dd` from a rooted boot).

## Running it against a different ROM

The staged vendor HALs come from the stock Android 13 firmware
(`TKQ1.220915.002`), because that is the only vendor image this device has.
Decryption depends on the phone's **current** `/vendor` and on how its `/data`
was encrypted, so a phone running a newer ROM (for example Android 17 on the
same 5.15-android13 kernel) only works as-is while it still carries the stock
vendor crypto stack: AIDL `android.hardware.security.keymint` **2.0** plus HIDL
`android.hardware.gatekeeper@1.0`. The kernel is not a concern here: this device's recovery partition is
ramdisk-only (stock `recovery.img`: `kernel_size = 0`,
`ramdisk_size = 56129408`), so the bootloader supplies the kernel from the
phone's own `boot.img`. That also means the ramdisk runs on whatever kernel
the phone currently has.

Check the live device before flashing:

```bash
adb shell getprop ro.vendor.build.fingerprint
adb shell ls /vendor/bin/hw | grep -Ei 'keymint|gatekeeper|keymaster|qseecom'
adb shell cat /vendor/etc/vintf/manifest/*keymint*.xml
adb shell grep -E 'userdata|metadata' /vendor/etc/fstab.qcom
adb shell getprop ro.crypto.state; adb shell getprop ro.crypto.type
```

* `keymint` manifest with `<version>2</version>` and
  `android.hardware.gatekeeper@1.0-service-qti` → this tree is already correct.
* `<version>3</version>`/`<version>4</version>`, an AIDL gatekeeper
  (`android.hardware.gatekeeper-rust-service-qti`) or a different
  `fileencryption=`/`keydirectory=` line → re-stage from the phone itself:

  ```bash
  scripts/pull-device-blobs.py            # adb pull + dependency closure
  python3 scripts/check-tree.py           # must print status: passed
  ```

  The script pulls only the crypto services, their ELF closure and the matching
  `*.rc`/VINTF fragments into `device/nubia/nx729j/recovery/root`, and writes a
  report to `dist/pulled-device/report.json`.

TWRP 16's vold only understands LSKF-based synthetic passwords (`spblob` v2/v3,
no Weaver protector). A ROM that switched `/data` to a Weaver protector would
need a different recovery stack.

## How decryption works on this device

TWRP 16 does not carry its own crypto implementation. The chain is:

```
TWRP UI (gui2 twrp_decrypt_backend)
  → android::keystore::Decrypt_User()            (bootable/recovery + libvold, static)
      → fscrypt_init_user0() / fscrypt_initialize_systemwide_keys()
          → keystore2 (built into the ramdisk, /system/bin/keystore2)
              → android.hardware.security.keymint  AIDL 2.0  IKeyMintDevice/default
                  → vendor/bin/hw/android.hardware.security.keymint-service-qti
                      → libqtikeymint.so → /dev/qseecom → TEE
              → android.hardware.gatekeeper  HIDL 1.0  IGatekeeper/default
                  → vendor/bin/hw/android.hardware.gatekeeper@1.0-service-qti
      → dm-default-key (metadata encryption) + inlinecrypt (userdata)
```

What this repository contributes to that chain:

* `TW_INCLUDE_CRYPTO`, `TW_INCLUDE_CRYPTO_FBE`, `TW_INCLUDE_FBE_METADATA_DECRYPT`,
  `BOARD_USES_METADATA_PARTITION`, `TW_USE_FSCRYPT_POLICY := 2` — so the build
  compiles `keystore2`, `libvold`'s crypto code and `fscryptpolicyget` into the
  ramdisk (`bootable/recovery/prebuilt/Android.mk`).
* `recovery.fstab` with the stock `/data` descriptor
  `fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized+wrappedkey_v0`,
  `keydirectory=/metadata/vold/metadata_encryption`,
  `metadata_encryption=aes-256-xts:wrappedkey_v0`, plus `/metadata` as a
  first-stage `wrappedkey` mount.
* The stock **KeyMint AIDL 2.0** HAL, **gatekeeper HIDL 1.0** HAL and `qseecomd`
  with their full vendor-side ELF dependency closure (`provenance/`).
* A recovery VINTF manifest declaring exactly the two HAL instances above.
  `keystore2` resolves KeyMint through
  `binder::get_declared_instances()`, i.e. it reads this manifest, and it uses
  AIDL KeyMint V2 natively (`Some(200) => "KeyMint device is current version"`).
  `libvold`'s `Decrypt.cpp` falls back to HIDL gatekeeper when no AIDL
  gatekeeper instance is declared, which is why the stock HIDL service works.
* `init.recovery.qcom.rc` starts `vendor.qseecomd`, then `gatekeeper-1-0` and
  `vendor.keymint-qti` from `on post-fs` (stock init never starts gatekeeper:
  it is `class early_hal`, and recovery only `class_start`s `default` and `hal`),
  and grants `/dev/qseecom` to `system:drmrpc` both in `ueventd.rc` and in `on fs`.
* `keymaster_ver=4.x` in `system.prop`, so TWRP does not probe the vendor
  manifest for a keymaster generation that only exists as AIDL.

The kernel side already supports all of it (`vendor_boot`'s GKI 5.15.41 config):
`CONFIG_BLK_INLINE_ENCRYPTION`, `CONFIG_BLK_INLINE_ENCRYPTION_FALLBACK`,
`CONFIG_DM_DEFAULT_KEY`, `CONFIG_DM_CRYPT`, `CONFIG_FS_ENCRYPTION_INLINE_CRYPT`,
`CONFIG_SCSI_UFS_CRYPTO`. `qcrypto-msm_dlkm.ko` is loaded by TWRP so the QTI
inline-crypto engine is available; the kernel's software fallback covers a
failure to load it.

## Notes and limitations

* **Untested on hardware.** Static checks pass, but boot, touch, display,
  decryption and flashing must be confirmed on a device.
* **Ramdisk-only image.** That is the shape of the stock partition, not a
  shortcut. Reading the first 4 KiB of `/dev/block/by-name/recovery_a` gives
  `header_version 4`, `header_size 1584`, `kernel_size = 0`,
  `ramdisk_size = 56129408`, and the stock `vendor/etc/fstab.qcom` declares
  `/recovery emmc slotselect,avb=vbmeta`. `boot.img` is kernel-only
  (`ramdisk_size = 0`), so the bootloader pairs the boot kernel with the
  recovery ramdisk. An early build of this tree embedded the GKI kernel from
  `boot.img` instead; it did not come up on hardware, which is why the tree now
  always sets `BOARD_EXCLUDE_KERNEL_FROM_RECOVERY_IMAGE := true`.
* **No SELinux policy is built.** TWRP recovery runs permissive, so the stock
  vendor HALs are not blocked by policy. Do not copy this tree into a build that
  enforces SELinux without adding policy for the three vendor services.
* **Kernel modules:** only the modules that recovery actually loads are staged
  (`TW_LOAD_VENDOR_MODULES`: `adsp_loader_dlkm.ko`, `qti_battery_charger.ko`,
  `qcrypto-msm_dlkm.ko`) plus their `modules.dep` closure and the UFS-PHY, PMIC
  ADC/temp-alarm and `gpio_keys_nubia` modules — 23 `.ko`, ~1.8 MiB. The other
  234 stock `vendor_dlkm` modules (Wi-Fi, camera, kgsl, video, ~91 MiB) are not
  needed by recovery and were dropped so the image stays far below the 100 MiB
  partition limit. Everything else (display, touch, UFS) is built into the stock
  kernel, which is why the existing NX729J tree also loads only two modules.
* **No QCOM `bootctrl` module.** The TWRP 16 manifest has no
  `hardware/qcom-caf/bootctrl`, so slot handling uses the generic AOSP boot HAL
  that `bootable/recovery` installs when `AB_OTA_UPDATER := true`. Flashing to
  an explicit slot (`fastboot flash recovery_ab`) does not need it.
* **Virtual A/B.** `ro.virtual_ab.enabled=true`; TWRP's `/data` handling assumes
  checkpoint/`dm-default-key` metadata encryption as shipped.
* If decryption fails on device, the first thing to capture is
  `adb shell dmesg` + `adb logcat` from recovery plus `/tmp/recovery.log`, and
  to check that `qseecomd`, `gatekeeper-1-0` and `vendor.keymint-qti` appear in
  `getprop | grep init.svc`.

## Blob provenance

`provenance/blobs-nx729j.json` lists every staged vendor object with its size,
SONAME, `DT_NEEDED` list and SHA-256, taken from
`RandomPush/nubia_nx729j_dump@e9f5a2b43408294358ec3c736b092942b4613609`
(fingerprint `nubia/NX729J/NX729J:13/TKQ1.220915.002/...`). It also records the
20 libraries that are intentionally *not* staged because the TWRP build provides
them. `scripts/fetch-blobs.py` regenerates the set from the dump listing.

## Credits

* YARP / TWRP 16 sources: [TWRP-Test](https://github.com/TWRP-Test)
* NX729J TWRP bring-up and stock kernel modules: BigfootACA's
  `device_nubia_nx729j` tree and the twrpdtgen output
* QCOM SM8850 YARP reference tree: `TWRP-Test/twrp_device_xiaomi_sm8850`
* Stock firmware dump: `RandomPush/nubia_nx729j_dump`
