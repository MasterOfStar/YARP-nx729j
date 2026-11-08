"""Extract the kernel from a stock Android boot image (header v3/v4).

The YARP recovery image for NX729J is built ramdisk-only
(BOARD_EXCLUDE_KERNEL_FROM_RECOVERY_IMAGE), which is what the existing NX729J
tree and the SM8850 YARP tree do. If the bootloader on your unit refuses to
boot a kernel-less recovery image, run this script against the stock boot.img
and rebuild with the extracted kernel:

    python3 scripts/extract-boot-kernel.py boot.img device/nubia/nx729j/prebuilt/kernel

then in device/nubia/nx729j/BoardConfig.mk

    TARGET_PREBUILT_KERNEL := $(DEVICE_PATH)/prebuilt/kernel
    # BOARD_EXCLUDE_KERNEL_FROM_RECOVERY_IMAGE := true   <- comment out

and rebuild. The kernel is copied verbatim, so the recovery runs on exactly the
kernel the stock firmware ships.
"""

import hashlib
import struct
import sys
from pathlib import Path

HEADER_SIZES = {3: 1580, 4: 1584}


def main():
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} <boot.img> <out-kernel>")
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    data = source.read_bytes()
    if data[:8] != b"ANDROID!":
        raise SystemExit("not an Android boot image (missing ANDROID! magic)")
    kernel_size, ramdisk_size, os_version, header_size = struct.unpack_from("<4I", data, 8)
    header_version = struct.unpack_from("<I", data, 40)[0]
    expected = HEADER_SIZES.get(header_version)
    if expected is None:
        raise SystemExit(f"unsupported boot header version {header_version} (need 3 or 4)")
    if header_size != expected:
        raise SystemExit(f"unexpected header size {header_size} for v{header_version}")
    if kernel_size == 0:
        raise SystemExit("this boot image carries no kernel (kernel_size == 0)")
    kernel = data[header_size:header_size + kernel_size]
    if len(kernel) != kernel_size:
        raise SystemExit("boot image is truncated inside the kernel")
    if kernel[:2] != b"MZ" and kernel[:4] != b"\x7fELF" and not kernel.startswith(b"\x00\x00\xa0\xe1"):
        # arm64 Image starts with a branch instruction; accept it but warn.
        print("warning: kernel does not look like an arm64 Image/ELF, copying anyway",
              file=sys.stderr)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(kernel)
    print(f"header_version={header_version} kernel_bytes={kernel_size} "
          f"ramdisk_bytes={ramdisk_size} os_version={os_version}")
    print(f"wrote {target} sha256={hashlib.sha256(kernel).hexdigest()}")


if __name__ == "__main__":
    main()
