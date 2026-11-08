#!/usr/bin/env python3
"""Fetch NX729J crypto HAL blobs + transitive vendor ELF dependency closure.

Reads the stock dump file list (all_files.txt) to resolve library names, then
downloads every ELF that lives on the vendor side.  System-side libraries are
recorded only: the TWRP-16 recovery build provides them (see
bootable/recovery/prebuilt/Android.mk crypto branch).
"""
import hashlib
import json
import os
import struct
import subprocess
import sys

SHA = "e9f5a2b43408294358ec3c736b092942b4613609"
BASE = "https://cdn.jsdelivr.net/gh/RandomPush/nubia_nx729j_dump@" + SHA
ROOT = os.environ.get("BLOB_OUT", "device/nubia/nx729j/recovery/root")
LISTING = os.environ.get("DUMP_LISTING", "all_files.txt")

# search order for a DT_NEEDED name (first match wins)
SEARCH = [
    "vendor/lib64/{n}",
    "vendor/lib64/hw/{n}",
    "system/system/lib64/{n}",
    "system/system/lib64/hw/{n}",
    "system_ext/lib64/{n}",
    "system/system/apex/com.android.vndk.current/lib64/{n}",
    "system_ext/apex/com.android.vndk.v33/lib64/{n}",
    "system_ext/apex/com.android.vndk.v32/lib64/{n}",
]

SEEDS = [
    "vendor/bin/hw/android.hardware.gatekeeper@1.0-service-qti",
    "vendor/bin/hw/android.hardware.security.keymint-service-qti",
    "vendor/bin/qseecomd",
]

# dlopen()ed HAL implementations (libhardware) -- not visible as DT_NEEDED
DLOPEN = [
    "vendor/lib64/hw/android.hardware.gatekeeper@1.0-impl-qti.so",
    "vendor/lib64/hw/vendor.qti.hardware.qseecom@1.0-impl.so",
]


def parse_elf_needed(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"\x7fELF":
        return None, None
    is64 = data[4] == 2
    if not is64:
        raise ValueError("32-bit ELF not expected: " + path)
    e_machine = struct.unpack_from("<H", data, 18)[0]
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", data, 0x3A)[0]
    e_shnum = struct.unpack_from("<H", data, 0x3C)[0]
    e_shstrndx = struct.unpack_from("<H", data, 0x3E)[0]

    def sh(i):
        off = e_shoff + i * e_shentsize
        return struct.unpack_from("<IIQQQQIIQQ", data, off)

    sections = [sh(i) for i in range(e_shnum)]
    shstr = sections[e_shstrndx]
    shstr_data = data[shstr[4]:shstr[4] + shstr[5]]

    def name_at(off):
        end = shstr_data.index(b"\0", off)
        return shstr_data[off:end].decode()

    dynsym = dynstr = None
    for s in sections:
        n = name_at(s[0])
        if n == ".dynsym":
            dynsym = s
        elif n == ".dynstr":
            dynstr = s
    if dynsym is None or dynstr is None:
        return e_machine, ([], None)
    strtab = data[dynstr[4]:dynstr[4] + dynstr[5]]
    syms = data[dynsym[4]:dynsym[4] + dynsym[5]]
    entsize = dynsym[9] or 24
    needed, soname = [], None
    for i in range(len(syms) // entsize):
        st_name, _info, _other, _shndx, _val, _size = struct.unpack_from(
            "<IBBHQQ", syms, i * entsize)
        if st_name == 0:
            continue
        end = strtab.index(b"\0", st_name)
        nm = strtab[st_name:end].decode()
        if nm:
            needed.append(nm)
    # DT_NEEDED lives in .dynamic; simpler: re-read via dynamic section
    return e_machine, needed


def dynamic_entries(path):
    """Return (needed_list, soname) from PT_DYNAMIC / .dynamic."""
    with open(path, "rb") as f:
        data = f.read()
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", data, 0x3A)[0]
    e_shnum = struct.unpack_from("<H", data, 0x3C)[0]
    e_shstrndx = struct.unpack_from("<H", data, 0x3E)[0]

    def sh(i):
        return struct.unpack_from("<IIQQQQIIQQ", data, e_shoff + i * e_shentsize)

    sections = [sh(i) for i in range(e_shnum)]
    shstr = sections[e_shstrndx]
    shstr_data = data[shstr[4]:shstr[4] + shstr[5]]
    names = {}
    for i, s in enumerate(sections):
        end = shstr_data.index(b"\0", s[0])
        names[i] = shstr_data[s[0]:end].decode()

    dyn = strtab = None
    for i, s in enumerate(sections):
        if names[i] == ".dynamic":
            dyn = s
        elif names[i] == ".dynstr":
            strtab = s
    if dyn is None or strtab is None:
        return [], None
    stab = data[strtab[4]:strtab[4] + strtab[5]]
    off, end = dyn[4], dyn[4] + dyn[5]
    needed, soname = [], None
    while off + 16 <= end:
        tag, val = struct.unpack_from("<qQ", data, off)
        off += 16
        if tag == 0:
            break
        if tag == 1:  # DT_NEEDED
            e = stab.index(b"\0", val)
            needed.append(stab[val:e].decode())
        elif tag == 14:  # DT_SONAME
            e = stab.index(b"\0", val)
            soname = stab[val:e].decode()
    return needed, soname


def available(path):
    return os.path.isfile(path)


def download(rel):
    dst = os.path.join(ROOT, rel)
    if os.path.isfile(dst) and os.path.getsize(dst) > 0:
        return True
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    url = BASE + "/" + rel
    r = subprocess.run(["curl", "-sSL", "--retry", "2", "--max-time", "90",
                        "-o", dst, url], capture_output=True)
    if r.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) < 64:
        if os.path.isfile(dst):
            os.unlink(dst)
        return False
    return True


def resolve(name, listing):
    for pat in SEARCH:
        p = pat.format(n=name)
        if p in listing:
            return p
    return None


def main():
    listing = set()
    with open(LISTING) as f:
        for line in f:
            line = line.strip()
            if line:
                listing.add(line)

    queue = list(SEEDS) + list(DLOPEN)
    seen = set()
    manifest = {}
    unresolved = {}
    system_needed = set()

    while queue:
        rel = queue.pop(0)
        if rel in seen:
            continue
        seen.add(rel)
        if not download(rel):
            unresolved[rel] = "download failed"
            continue
        path = os.path.join(ROOT, rel)
        try:
            needed, soname = dynamic_entries(path)
        except Exception as e:  # noqa: BLE001
            unresolved[rel] = "elf parse: %s" % e
            continue
        manifest[rel] = {
            "size": os.path.getsize(path),
            "sha256": hashlib.sha256(open(path, "rb").read()).hexdigest(),
            "soname": soname,
            "needed": needed,
        }
        for n in needed:
            src = resolve(n, listing)
            if src is None:
                unresolved.setdefault(rel, []).append(n) if isinstance(
                    unresolved.get(rel), list) else unresolved.update({rel: [n]})
                continue
            if src.startswith("vendor/"):
                if src not in seen:
                    queue.append(src)
            else:
                system_needed.add(src)

    report_path = os.environ.get("BLOB_REPORT", "provenance/blobs-fetch-report.json")
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    with open(report_path, "w") as f:
        json.dump({"files": manifest, "system_provided": sorted(system_needed),
                   "unresolved": unresolved}, f, indent=2, sort_keys=True)

    total = sum(v["size"] for v in manifest.values())
    print("staged %d files, %.1f MiB" % (len(manifest), total / 1048576))
    print("system-provided deps: %d" % len(system_needed))
    for s in sorted(system_needed):
        print("  SYS", s)
    if unresolved:
        print("UNRESOLVED:")
        for k, v in unresolved.items():
            print("  ", k, v)


if __name__ == "__main__":
    main()
