"""Pull the crypto HALs that are actually installed on the connected device.

The staged blobs in this tree come from the stock Android 13 firmware. A device
running a newer ROM may ship a different KeyMint generation (for example AIDL
KeyMint 3/4 plus an AIDL gatekeeper instead of HIDL 1.0), and then decryption
fails with "Vold unable to connect to keystore2" or a keymaster version
mismatch. This script reads the live device over adb, resolves the ELF
dependency closure of the crypto services and writes everything into
`device/nubia/nx729j/recovery/root`, so the tree matches the phone it will run
on.

Usage:
    scripts/pull-device-blobs.py                 # pull and install
    scripts/pull-device-blobs.py --dry-run       # report only
    scripts/pull-device-blobs.py --out /tmp/pull # keep raw files elsewhere

It never modifies the device.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

REPO = Path(__file__).resolve().parent.parent
DEVICE = REPO / "device/nubia/nx729j"
ROOT = DEVICE / "recovery/root"

SEEDS = [
    "vendor/bin/qseecomd",
    "vendor/bin/hw/android.hardware.security.keymint-service-qti",
    "vendor/bin/hw/android.hardware.security.onekeymint-service-qti",
    "vendor/bin/hw/android.hardware.gatekeeper@1.0-service-qti",
    "vendor/bin/hw/android.hardware.gatekeeper-rust-service-qti",
    "vendor/bin/hw/android.hardware.keymaster@4.0-service-qti",
]

SEARCH_DIRS = [
    "vendor/lib64",
    "vendor/lib64/hw",
    "odm/lib64",
    "odm/lib64/hw",
    "system/lib64",
    "system/lib64/hw",
    "system_ext/lib64",
    "system/apex/com.android.vndk.current/lib64",
]

# Libraries the TWRP 16 recovery ramdisk already provides (do not pull these).
BUILD_PROVIDED = {
    "libbase.so", "libbinder_ndk.so", "libc++.so", "libc.so", "libcrypto.so",
    "libcutils.so", "libdl.so", "libdmabufheap.so", "libhardware.so",
    "libhidlbase.so", "libhidlmemory.so", "libion.so", "liblog.so", "libm.so",
    "libutils.so", "libxml2.so", "libandroidicu.so", "libicuuc.so",
    "libicui18n.so", "libcrypto_utils.so",
}


def adb(*args, check=True):
    return subprocess.run(["adb", *args], capture_output=True, check=check)


def adb_shell(command):
    result = adb("shell", command, check=False)
    return result.stdout.decode("utf-8", "replace")


def dynamic_entries(path):
    """Return (soname, [needed]) from an ELF64 little-endian shared object."""
    data = path.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        return None, []
    shoff = struct.unpack_from("<Q", data, 0x28)[0]
    shentsize = struct.unpack_from("<H", data, 0x3A)[0]
    shnum = struct.unpack_from("<H", data, 0x3C)[0]
    sections = []
    for i in range(shnum):
        base = shoff + i * shentsize
        name, stype, _, _, offset, size, link, _, _, entsize = struct.unpack_from(
            "<IIQQQQIIQQ", data, base)
        sections.append({"type": stype, "offset": offset, "size": size,
                         "link": link, "entsize": entsize})
    dynamic = next((s for s in sections if s["type"] == 6), None)
    if dynamic is None:
        return None, []
    strtab = sections[dynamic["link"]]
    strings = data[strtab["offset"]:strtab["offset"] + strtab["size"]]
    soname, needed = None, []
    step = dynamic["entsize"] or 16
    for offset in range(dynamic["offset"], dynamic["offset"] + dynamic["size"], step):
        tag, value = struct.unpack_from("<qQ", data, offset)
        if tag == 0:
            break
        end = strings.find(b"\0", value)
        text = strings[value:end].decode("utf-8", "replace")
        if tag == 1:
            needed.append(text)
        elif tag == 14:
            soname = text
    return soname, needed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(REPO / "dist/pulled-device"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if shutil.which("adb") is None:
        raise SystemExit("adb is not installed")
    state = adb("get-state", check=False).stdout.strip().decode()
    if state != "device":
        raise SystemExit(f"no usable device: adb get-state -> {state or '(empty)'}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fingerprint = adb_shell("getprop ro.vendor.build.fingerprint").strip()
    release = adb_shell("getprop ro.build.version.release").strip()
    keymaster = adb_shell("getprop ro.vendor.build.version.sdk").strip()
    print(f"device: {fingerprint or '(no fingerprint)'} release={release} vendor_sdk={keymaster}")

    # Inventory the library directories once instead of one ls per lookup.
    inventory = {}
    for directory in SEARCH_DIRS:
        listing = adb_shell(f"ls /{directory} 2>/dev/null")
        for name in listing.split():
            inventory.setdefault(name, f"/{directory}/{name}")

    queue = list(SEEDS)
    pulled, missing = [], []
    while queue:
        remote = queue.pop(0)
        local = out / remote
        if not local.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            result = adb("pull", f"/{remote}", str(local), check=False)
            if result.returncode != 0:
                missing.append(remote)
                continue
        soname, needed = dynamic_entries(local)
        pulled.append({
            "path": remote,
            "size": local.stat().st_size,
            "sha256": hashlib.sha256(local.read_bytes()).hexdigest(),
            "soname": soname,
            "needed": needed,
        })
        for library in needed:
            if library in BUILD_PROVIDED:
                continue
            if library in inventory:
                candidate = inventory[library].lstrip("/")
                if candidate not in [entry["path"] for entry in pulled] and \
                        candidate not in queue:
                    queue.append(candidate)
            else:
                missing.append(library)

    manifest_dir = out / "vendor/etc/vintf/manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    for name in adb_shell("ls /vendor/etc/vintf/manifest 2>/dev/null").split():
        if re.search(r"keymint|gatekeeper|keymaster|weaver|secretkeeper|sharedsecret",
                     name):
            adb("pull", f"/vendor/etc/vintf/manifest/{name}",
                str(manifest_dir / name), check=False)
    for name in adb_shell("ls /vendor/etc/init 2>/dev/null").split():
        if re.search(r"keymint|gatekeeper|keymaster|qseecom|weaver", name):
            target = out / "vendor/etc/init" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            adb("pull", f"/vendor/etc/init/{name}", str(target), check=False)
    adb("pull", "/vendor/etc/vintf/manifest.xml",
        str(out / "vendor/etc/vintf/manifest.xml"), check=False)

    report = {
        "device": {"fingerprint": fingerprint, "release": release},
        "files": pulled,
        "missing": sorted(set(missing)),
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"pulled {len(pulled)} files, unresolved {len(report['missing'])}")
    for name in report["missing"]:
        print(f"  unresolved: {name}")

    if args.dry_run:
        print(f"dry run: leaving {out} untouched, nothing installed")
        return 0

    for entry in pulled:
        source = out / entry["path"]
        destination = ROOT / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    for directory in ("vendor/etc/init", "vendor/etc/vintf"):
        source = out / directory
        if not source.is_dir():
            continue
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            destination = ROOT / directory / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    print(f"installed into {ROOT}")
    print("next: python3 scripts/check-tree.py   (expects status: passed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
