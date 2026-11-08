"""Fetch the stock NX729J boot image and extract its kernel.

The stock firmware ships a GKI 5.15.41 kernel in `boot.img` (ramdisk_size == 0,
the generic ramdisk lives in `init_boot`). Placing that exact kernel into
`recovery.img` reproduces the shape of the stock recovery image, which is the
safest target for the bootloader; `BoardConfig.mk` falls back to a kernel-less
(ramdisk-only) image when `prebuilt/kernel` is absent.

The dump repository ignores `recovery.img` and `modem.img`, so `boot.img` is the
authoritative kernel source. It is fetched through the Git blob API because the
CDN refuses files this large.
"""

import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / "sources.json"
DEFAULT_TARGET = REPO / "device/nubia/nx729j/prebuilt/kernel"
HEADER_SIZES = {3: 1580, 4: 1584}


def fetch(url, destination):
    subprocess.run([
        "curl", "-sSL", "--retry", "5", "--retry-delay", "5", "--max-time", "900",
        "-H", "Accept: application/vnd.github.raw",
        url, "-o", str(destination),
    ], check=True)


def main():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    firmware = lock.get("stock_firmware")
    if not firmware:
        raise SystemExit("sources.json has no stock_firmware entry")
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TARGET

    url = (f"https://api.github.com/repos/{firmware['repository']}/git/blobs/"
           f"{firmware['boot_img_blob_sha']}")
    local = os.environ.get("YARP_BOOT_IMG")
    with tempfile.TemporaryDirectory() as workdir:
        if local:
            boot_img = Path(local)
            print(f"using local boot image {boot_img}")
        else:
            boot_img = Path(workdir) / "boot.img"
            print(f"downloading {firmware['repository']} boot.img ...")
            fetch(url, boot_img)
        data = boot_img.read_bytes()
        if len(data) != firmware["boot_img_size"]:
            raise SystemExit(f"boot.img size mismatch: {len(data)}")
        if data[:8] != b"ANDROID!":
            raise SystemExit("boot.img is not an Android boot image")
        kernel_size, ramdisk_size, _, header_size = struct.unpack_from("<4I", data, 8)
        header_version = struct.unpack_from("<I", data, 40)[0]
        if header_size != HEADER_SIZES.get(header_version):
            raise SystemExit(f"unexpected boot header v{header_version} size {header_size}")
        if kernel_size == 0:
            raise SystemExit("stock boot.img carries no kernel")
        kernel = data[header_size:header_size + kernel_size]

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(kernel)
    digest = hashlib.sha256(kernel).hexdigest()
    expected = firmware.get("kernel_sha256")
    print(f"header_version={header_version} kernel_bytes={kernel_size} "
          f"ramdisk_bytes={ramdisk_size}")
    print(f"wrote {target} sha256={digest}")
    if expected and digest != expected:
        raise SystemExit(f"kernel checksum mismatch: expected {expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
