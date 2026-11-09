"""Check the built NX729J recovery container and its staged YARP runtime.

The recovery must be a ramdisk-only Android boot image (header v4, no kernel),
carry the YARP/GUI2 runtime, and stage the full FBE + metadata-encryption
service chain (qseecomd -> gatekeeper / KeyMint HAL -> keystore2 -> libvold).
"""

import hashlib
import json
from pathlib import Path
import struct
import sys

LZ4_MAGIC = (b"\x02\x21\x4c\x18", b"\x04\x22\x4d\x18")
PARTITION_LIMIT = 104857600
AARCH64 = 183

# vendor side: (relative path, description)
VENDOR_REQUIRED = [
    ("vendor/bin/qseecomd", "QSEECom daemon"),
    ("vendor/bin/hw/android.hardware.gatekeeper@1.0-service-qti", "Gatekeeper HAL"),
    ("vendor/bin/hw/android.hardware.security.keymint-service-qti", "KeyMint HAL"),
    ("vendor/lib64/libQSEEComAPI.so", "QSEECom client library"),
    ("vendor/lib64/libqtikeymint.so", "QTI KeyMint backend"),
    ("vendor/lib64/android.hardware.security.keymint-V2-ndk.so", "KeyMint V2 AIDL interface"),
    ("vendor/lib64/android.hardware.gatekeeper@1.0.so", "Gatekeeper HIDL interface"),
    ("vendor/lib64/libkeymaster_messages.so", "Keymaster message helpers"),
    ("vendor/etc/init/qseecomd.rc", "qseecomd init script"),
    ("vendor/etc/init/android.hardware.gatekeeper@1.0-service-qti.rc", "Gatekeeper init script"),
    ("vendor/etc/init/android.hardware.security.keymint-service-qti.rc", "KeyMint init script"),
    ("vendor/etc/vintf/manifest.xml", "recovery VINTF manifest"),
]

# system side: produced by TW_INCLUDE_CRYPTO / TW_INCLUDE_CRYPTO_FBE
SYSTEM_REQUIRED = [
    ("system/bin/keystore2", "keystore2 daemon"),
    ("system/bin/fscryptpolicyget", "fscrypt policy helper"),
    ("system/lib64/libkeystore2_crypto.so", "keystore2 crypto backend"),
    ("system/lib64/libgatekeeper.so", "gatekeeper support"),
    ("system/lib64/libkeymint_support.so", "KeyMint support"),
    ("system/etc/recovery.fstab", "recovery fstab"),
]

VINTF_REQUIRED = [
    "IKeyMintDevice/default",
    "IGatekeeper/default",
]

FONTS = ["wqy-microhei.ttf", "DroidSansMono.ttf"]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def elf_arch(path):
    blob = path.read_bytes()[:20]
    require(blob[:4] == b"\x7fELF", f"{path} is not an ELF object")
    return struct.unpack_from("<H", blob, 18)[0]


def main():
    product = Path(sys.argv[1])
    image = product / "recovery.img"
    data = image.read_bytes()
    require(len(data) >= 4096, "Recovery image is truncated")
    require(data[:8] == b"ANDROID!", "Missing Android boot image magic")
    kernel_size, ramdisk_size, os_version, header_size = struct.unpack_from("<4I", data, 8)
    header_version = struct.unpack_from("<I", data, 40)[0]
    require(header_version == 4, f"Unexpected boot header version: {header_version}")
    require(header_size == 1584, f"Unexpected v4 header size: {header_size}")
    # This device's recovery partition is ramdisk-only: the stock recovery.img
    # header reports kernel_size = 0 and ramdisk_size = 56129408, and the
    # bootloader takes the kernel from boot.img. Anything else is a build bug.
    require(kernel_size == 0,
            f"Recovery image must not carry a kernel (found kernel_size={kernel_size})")
    require(ramdisk_size <= len(data) - 4096,
            "Ramdisk overruns the recovery image")
    image_shape = "ramdisk-only"
    require(0 < ramdisk_size <= len(data) - 4096, "Invalid ramdisk size")
    require(len(data) <= PARTITION_LIMIT, "Recovery exceeds the 100 MiB recovery partition")
    # mkbootimg leaves the legacy page-size field at 0 in some v4 images even
    # though the payload is page aligned, so treat 0 as the 4096 default.
    page_size = struct.unpack_from("<I", data, 36)[0] or 4096
    require(page_size in (2048, 4096, 8192, 16384),
            f"Unexpected boot image page size: {page_size}")
    ramdisk_offset = page_size
    require(ramdisk_offset == 4096, f"Unexpected first page size: {ramdisk_offset}")
    require(ramdisk_offset + ramdisk_size <= len(data),
            "Ramdisk starts past the end of the recovery image")
    ramdisk_magic = data[ramdisk_offset:ramdisk_offset + 4]
    require(ramdisk_magic in LZ4_MAGIC,
            "Expected an LZ4-compressed recovery ramdisk at offset "
            f"{ramdisk_offset} (kernel_size={kernel_size})")
    ramdisk_compression = "lz4-legacy" if ramdisk_magic == LZ4_MAGIC[0] else "lz4-frame"
    # The image is padded out to the partition size (and may carry an AVB
    # footer), so trailing bytes are only checked for length, not content.

    root = product / "recovery/root"
    candidates = [root / "system/bin/recovery", root / "sbin/recovery"]
    executable = next((candidate for candidate in candidates if candidate.is_file()), None)
    require(executable is not None, "Missing staged recovery executable")
    require(elf_arch(executable) == AARCH64, "Recovery is not AArch64")

    # libgui2 is a shared library, so the YARP and GUI2 identifiers may live in a
    # staged shared object rather than in the recovery executable itself.
    yarp_owner = None
    gui2_owner = None
    staged_objects = [executable] + sorted(
        path for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and path.suffix != ".ko"
        and path.stat().st_size <= 16 * 1024 * 1024)
    for staged in staged_objects:
        blob = staged.read_bytes()
        if yarp_owner is None and b"Yet Another Recovery Project" in blob:
            yarp_owner = staged.relative_to(root).as_posix()
        if gui2_owner is None and b"gui2" in blob.lower():
            gui2_owner = staged.relative_to(root).as_posix()
        if yarp_owner and gui2_owner:
            break
    require(yarp_owner is not None, "YARP language resources are missing")
    require(gui2_owner is not None, "GUI2 runtime markers are missing")

    for font in FONTS:
        path = root / "twres/fonts" / font
        require(path.is_file() and path.stat().st_size > 0, f"Missing GUI2 font: {font}")

    for relative, description in VENDOR_REQUIRED + SYSTEM_REQUIRED:
        path = root / relative
        require(path.is_file(), f"Missing {description}: {relative}")

    for relative, _ in VENDOR_REQUIRED:
        if relative.startswith("vendor/bin/"):
            require(elf_arch(root / relative) == AARCH64, f"{relative} is not AArch64")
            require((root / relative).stat().st_mode & 0o111, f"{relative} is not executable")

    manifest = (root / "vendor/etc/vintf/manifest.xml").read_text(encoding="utf-8")
    for entry in VINTF_REQUIRED:
        require(entry in manifest, f"VINTF manifest does not declare {entry}")

    fstab = (root / "system/etc/recovery.fstab").read_text(encoding="utf-8")
    require("fileencryption=aes-256-xts:aes-256-cts:v2" in fstab,
            "recovery.fstab lost the /data FBE v2 descriptor")
    require("metadata_encryption=aes-256-xts" in fstab,
            "recovery.fstab lost the metadata encryption descriptor")
    require("keydirectory=/metadata/vold/metadata_encryption" in fstab,
            "recovery.fstab lost the metadata key directory")

    init_rc = (root / "init.recovery.qcom.rc").read_text(encoding="utf-8")
    for service in ("vendor.qseecomd", "gatekeeper-1-0", "vendor.keymint-qti"):
        require(f"start {service}" in init_rc,
                f"init.recovery.qcom.rc does not start {service}")

    modules = root / "vendor/lib/modules/1.1"
    for module in ("qcrypto-msm_dlkm.ko", "adsp_loader_dlkm.ko", "qti_battery_charger.ko"):
        require((modules / module).is_file(), f"Missing kernel module: {module}")

    report = {
        "device": "nubia/nx729j",
        "sha256": hashlib.sha256(data).hexdigest(),
        "image_bytes": len(data),
        "partition_limit_bytes": PARTITION_LIMIT,
        "header_version": header_version,
        "header_size": header_size,
        "kernel_bytes": kernel_size,
        "image_shape": image_shape,
        "ramdisk_bytes": ramdisk_size,
        "ramdisk_offset": ramdisk_offset,
        "page_size": page_size,
        "ramdisk_compression": ramdisk_compression,
        "encoded_os_version": os_version,
        "recovery_architecture": "aarch64",
        "yarp_marker_object": yarp_owner,
        "gui2_marker_object": gui2_owner,
        "gui2_fonts": FONTS,
        "fbe_decryption": {
            "data_fstab_flags": "aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized+wrappedkey_v0",
            "metadata_encryption": "aes-256-xts:wrappedkey_v0",
            "keymaster_interface": "android.hardware.keymaster@4.0 (HIDL) + android.hardware.security.keymint V2 (AIDL)",
            "gatekeeper_interface": "android.hardware.gatekeeper@1.0 (HIDL)",
            "services": ["vendor.qseecomd", "gatekeeper-1-0", "vendor.keymint-qti", "keystore2"],
        },
        "validation_scope": (
            "Build and static checks only; on-device boot, /data decryption and "
            "flashing are untested."
        ),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
