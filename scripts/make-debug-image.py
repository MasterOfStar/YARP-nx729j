#!/usr/bin/env python3
"""Repack an existing ramdisk-only YARP recovery image into a debug image.

Changes applied to the ramdisk:
  * system/etc/init/keystore2.rc: drop "critical window=0" so a crashing
    keystore2 no longer reboots the whole device.
  * add system/bin/yarp-logdump.sh + system/etc/init/yarp-logdump.rc so logcat
    and dmesg are periodically written to /data/local/tmp on the phone.

Everything else (boot header fields, os_version, AVB tail) is preserved.
"""
import ctypes, os, struct, sys, time

SRC = sys.argv[1]
DST = sys.argv[2]
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "device/nubia/nx729j/recovery/root")
# Files whose in-tree copy must win over the one baked into the base image.
SYNC = [
    "vendor/etc/init/qseecomd.rc",
    "vendor/etc/init/android.hardware.security.keymint-service-qti.rc",
    "vendor/etc/init/android.hardware.gatekeeper@1.0-service-qti.rc",
    "vendor/etc/vintf/manifest.xml",
    "system/etc/init/keystore2.rc",
]

LZ4_MAGIC = 0x184C2102
PAGE = 4096
BLOCK = 8 * 1024 * 1024


def lz4_decompress(src):
    lib = ctypes.CDLL("liblz4.so.1")
    lib.LZ4_decompress_safe.restype = ctypes.c_int
    lib.LZ4_decompress_safe.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    dst = bytearray()
    buf = ctypes.create_string_buffer(BLOCK)
    i, magic = 0, struct.unpack_from("<I", src, 0)[0]
    assert magic == LZ4_MAGIC, hex(magic)
    i = 4
    while i + 4 <= len(src):
        n = struct.unpack_from("<I", src, i)[0]
        i += 4
        if n == 0 or i + n > len(src):
            break
        got = lib.LZ4_decompress_safe(src[i:i + n], buf, n, BLOCK)
        assert got > 0, got
        dst += buf.raw[:got]
        i += n
    return bytes(dst)


def lz4_compress(data):
    lib = ctypes.CDLL("liblz4.so.1")
    lib.LZ4_compress_HC.restype = ctypes.c_int
    lib.LZ4_compress_HC.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int]
    bound = 8 * 1024 * 1024 + 65536
    out = bytearray(struct.pack("<I", LZ4_MAGIC))
    for off in range(0, len(data), BLOCK):
        chunk = data[off:off + BLOCK]
        buf = ctypes.create_string_buffer(bound)
        n = lib.LZ4_compress_HC(chunk, buf, len(chunk), bound, 9)
        assert n > 0, n
        out += struct.pack("<I", n) + buf.raw[:n]
    return bytes(out)


CPIO_FIELDS = ("ino", "mode", "uid", "gid", "nlink", "mtime", "filesize",
               "devmajor", "devminor", "rdevmajor", "rdevminor", "namesize", "check")


def cpio_read(blob):
    out, pos = [], 0
    while pos + 110 <= len(blob):
        if blob[pos:pos + 6] not in (b"070701", b"070702"):
            break
        h = blob[pos:pos + 110]
        vals = {k: int(h[6 + 8 * i:14 + 8 * i], 16) for i, k in enumerate(CPIO_FIELDS)}
        name_end = pos + 110 + vals["namesize"]
        name = blob[pos + 110:name_end].rstrip(b"\0").decode("utf-8", "replace")
        data_start = name_end + (-name_end) % 4
        data = blob[data_start:data_start + vals["filesize"]]
        out.append((name, vals, data))
        pos = data_start + vals["filesize"]
        pos += (-pos) % 4
        if name == "TRAILER!!!":
            break
    return out


def cpio_write(entries):
    buf = bytearray()
    for ino, (name, vals, data) in enumerate(entries, start=1):
        raw_name = name.encode() + b"\0"
        fields = dict(vals, ino=ino, namesize=len(raw_name), filesize=len(data))
        header = b"070701" + b"".join(b"%08X" % fields.get(k, 0) for k in CPIO_FIELDS)
        assert len(header) == 110
        buf += header + raw_name
        buf += b"\0" * ((-(110 + len(raw_name))) % 4)
        buf += data
        buf += b"\0" * ((-len(data)) % 4)
    return bytes(buf)


LOGDUMP_SH = b"""#!/system/bin/sh
# Written by YARP debug image: keep logcat/dmesg on disk so they survive a reboot.
D=/data/local/tmp
while [ ! -d /data/local/tmp ]; do sleep 1; done
ps -A > $D/ps.txt 2>&1
getprop > $D/props.txt 2>&1
{
  echo "== /dev/block/by-name"
  ls -l /dev/block/by-name 2>&1
  echo "== /proc/partitions"
  cat /proc/partitions 2>&1
  echo "== /dev/block/mapper"
  ls -l /dev/block/mapper 2>&1
  echo "== backlight"
  cat /sys/class/backlight/panel0-backlight/max_brightness 2>&1
  cat /sys/class/backlight/panel0-backlight/brightness 2>&1
  echo "== mount"
  mount 2>&1
} > $D/hw.txt 2>&1
while true; do
  logcat -b all -v threadtime -d > $D/lc.txt 2>&1
  dmesg > $D/dm.txt 2>&1
  cp /tmp/recovery.log $D/rec.log 2>/dev/null
  sleep 3
done
"""

LOGDUMP_RC = b"""service yarp-logdump /system/bin/sh /system/bin/yarp-logdump.sh
    class late_start
    user root
    group root system log
    seclabel u:r:recovery:s0

on boot
    start yarp-logdump
"""


def main():
    img = open(SRC, "rb").read()
    assert img[:8] == b"ANDROID!"
    kernel_size, ramdisk_size = struct.unpack_from("<II", img, 8)
    assert kernel_size == 0, kernel_size
    cpio = lz4_decompress(img[PAGE:PAGE + ramdisk_size])
    entries = cpio_read(cpio)
    print("cpio entries:", len(entries), "bytes:", len(cpio))

    changed = []
    now = int(time.time())
    for name, vals, data in entries:
        if False:
            keep = [l for l in data.decode().splitlines(True)
                    if "critical window" not in l and "Reboot to bootloader" not in l]
            vals["filesize"] = len("".join(keep).encode())
            entries[entries.index((name, vals, data))] = (
                name, vals, "".join(keep).encode())
            changed.append(name + " (critical removed)")
        elif name == "TRAILER!!!":
            pass

    entries = [e for e in entries if e[0] != "TRAILER!!!"]

    def substitute(path, data, mode):
        for i, (n, v, _) in enumerate(entries):
            if n == path:
                v = dict(v, filesize=len(data), mode=mode, mtime=now)
                entries[i] = (n, v, data)
                return
        add(path, data, mode)

    for rel in SYNC:
        src = os.path.join(ROOT, rel)
        substitute(rel, open(src, "rb").read(), 0o100644)

    def add(path, data, mode):
        entries.append((path, dict(ino=0, mode=mode, uid=0, gid=0, nlink=1,
                                   mtime=now, filesize=len(data), devmajor=0,
                                   devminor=0, rdevmajor=0, rdevminor=0,
                                   namesize=0), data))
        changed.append(path)

    add("system/bin/yarp-logdump.sh", LOGDUMP_SH, 0o100755)
    add("system/etc/init/yarp-logdump.rc", LOGDUMP_RC, 0o100644)
    entries.append(("TRAILER!!!", dict(ino=0, mode=0, uid=0, gid=0, nlink=1,
                                       mtime=0, filesize=0, devmajor=0,
                                       devminor=0, rdevmajor=0, rdevminor=0,
                                       namesize=0), b""))

    new_cpio = cpio_write(entries)
    new_ramdisk = lz4_compress(new_cpio)
    print("new cpio:", len(new_cpio), "new ramdisk (lz4):", len(new_ramdisk))

    header = bytearray(img[:1584])
    struct.pack_into("<I", header, 12, len(new_ramdisk))
    footer_off = len(img) - 64
    tail_off = struct.unpack_from(">Q", img, footer_off + 20)[0]
    vb_size = struct.unpack_from(">Q", img, footer_off + 28)[0]
    vb_blob = img[tail_off:tail_off + vb_size]
    footer = bytearray(img[footer_off:])
    new_off = (PAGE + len(new_ramdisk) + PAGE - 1) // PAGE * PAGE
    struct.pack_into(">QQ", footer, 12, new_off, new_off)  # original_image_size, vbmeta_offset
    print("avb: old vbmeta@%d size %d -> new@%d" % (tail_off, vb_size, new_off))

    out = bytearray(header)
    out += b"\0" * (PAGE - len(header))
    out += new_ramdisk
    assert len(out) <= new_off, (len(out), new_off)
    out += b"\0" * (new_off - len(out))
    out += vb_blob
    out += b"\0" * (footer_off - len(out))
    out += bytes(footer)
    assert len(out) == len(img), (len(out), len(img))
    open(DST, "wb").write(bytes(out))
    print("wrote", DST, len(out))
    print("changed:", sorted(set(changed)))


main()
