"""Static validation of the NX729J YARP device tree.

Runs without an AOSP checkout so a broken tree fails in seconds instead of
after a 95 GiB source sync. Checks the build configuration, the recovery fstab,
the staged FBE/metadata-encryption service chain (ELF architecture, executable
bits and the DT_NEEDED closure) and the init/VINTF wiring.
"""

import json
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parent.parent
DEVICE = REPO / "device/nubia/nx729j"

AARCH64 = 183
# Libraries provided by the TWRP 16 recovery build itself (bootable/recovery
# prebuilt/Android.mk with TW_INCLUDE_CRYPTO*), or by the staged vendor set.
BUILD_PROVIDED = {
    "libc.so", "libm.so", "libdl.so", "liblog.so", "libutils.so", "libcutils.so",
    "libbase.so", "libhardware.so", "libhidlbase.so", "libhidlmemory.so",
    "libbinder_ndk.so", "libbinder.so", "libc++.so", "libcrypto.so",
    "libion.so", "libdmabufheap.so", "libxml2.so", "libz.so",
    "android.hidl.memory@1.0.so", "android.hidl.memory.token@1.0.so",
    "android.hidl.allocator@1.0.so",
    "android.hardware.security.keymint-V2-ndk.so",
    "android.hardware.security.keymint-V3-ndk.so",
    "android.hardware.security.keymint-V4-ndk.so",
    "android.hardware.security.secureclock-V1-ndk.so",
    "android.hardware.security.sharedsecret-V1-ndk.so",
    "android.hardware.gatekeeper-V1-ndk.so", "android.hardware.gatekeeper@1.0.so",
    "android.hardware.keymaster@3.0.so", "android.hardware.keymaster@4.0.so",
    "android.hardware.keymaster@4.1.so", "android.hardware.weaver@1.0.so",
    "android.hardware.weaver-V2-ndk.so", "android.system.keystore2-V5-ndk.so",
    "android.security.maintenance-ndk.so", "android.security.authorization-ndk.so",
    "android.security.apc-ndk.so", "android.security.compat-ndk.so",
    "libkeymaster_messages.so", "libkeymaster4support.so", "libkeymaster4_1support.so",
    "libkeymint_support.so", "libkeystore2_crypto.so", "libkeystore2_aaid.so",
    "libkeystore2_apc_compat.so", "libkeystore2_vintf_cpp.so",
    "libgatekeeper.so", "libgatekeeper_aidl.so", "libkm_compat.so",
    "libkm_compat_service.so", "libpuresoftkeymasterdevice.so",
    "libsoftkeymasterdevice.so", "libcppbor.so", "libcppbor_external.so",
    "libcppcose_rkp.so", "lib_android_keymaster_keymint_utils.so",
    "libprotobuf-cpp-lite.so", "libstatslog.so", "libsqlite.so",
    "libandroidicu.so", "libicuuc.so", "libicui18n.so", "libcrypto_utils.so",
}

BOARDCONFIG_EXPECTED = {
    "DEVICE_PATH": "device/nubia/nx729j",
    "TARGET_BOARD_PLATFORM": "nubia_sm8550",
    "BOARD_BOOT_HEADER_VERSION": "4",
    "BOARD_KERNEL_PAGESIZE": "4096",
    "BOARD_RAMDISK_USE_LZ4": "true",
    "BOARD_RECOVERYIMAGE_PARTITION_SIZE": "104857600",
    "TARGET_RECOVERY_FSTAB": "$(DEVICE_PATH)/recovery.fstab",
    "AB_OTA_UPDATER": "true",
    "TW_INCLUDE_CRYPTO": "true",
    "TW_INCLUDE_CRYPTO_FBE": "true",
    "TW_INCLUDE_FBE_METADATA_DECRYPT": "true",
    "BOARD_USES_METADATA_PARTITION": "true",
    "TW_USE_FSCRYPT_POLICY": "2",
    "TARGET_RECOVERY_QCOM_RTC_FIX": "true",
    "TW_LOAD_VENDOR_MODULES_EXCLUDE_GKI": "true",
}

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
    return condition


def read_make_vars(path):
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":=" not in line:
            continue
        name, _, value = line.partition(":=")
        values.setdefault(name.strip(), value.strip())
    return values


def dynamic_entries(path):
    """Return (soname, needed, rpath) from an ELF64 little-endian file."""
    data = path.read_bytes()
    if data[:4] != b"\x7fELF":
        raise ValueError(f"{path} is not an ELF object")
    is64 = data[4] == 2
    if not is64 or data[5] != 1:
        raise ValueError(f"{path} is not a 64-bit little-endian ELF object")
    e_machine = struct.unpack_from("<H", data, 18)[0]
    e_phoff = struct.unpack_from("<Q", data, 32)[0]
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 54)
    loads = []
    dynamic = None
    for index in range(e_phnum):
        base = e_phoff + index * e_phentsize
        p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from("<IIQQQQQQ", data, base)
        if p_type == 1:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == 2:
            dynamic = (p_offset, p_filesz)
    if dynamic is None:
        return e_machine, None, [], []

    def vaddr_to_offset(address):
        for p_vaddr, p_offset, p_filesz in loads:
            if p_vaddr <= address < p_vaddr + p_filesz:
                return p_offset + (address - p_vaddr)
        return None

    soname = None
    needed = []
    rpath = []
    offset, size = dynamic
    entries = []
    string_table_vaddr = None
    for position in range(offset, offset + size, 16):
        tag, value = struct.unpack_from("<qQ", data, position)
        if tag == 0:
            break
        entries.append((tag, value))
        if tag == 5:  # DT_STRTAB
            string_table_vaddr = value
    if string_table_vaddr is None:
        return e_machine, soname, needed, rpath
    string_table = vaddr_to_offset(string_table_vaddr)
    if string_table is None:
        return e_machine, soname, needed, rpath
    for tag, value in entries:
        # DT_NEEDED / DT_SONAME / DT_RPATH / DT_RUNPATH hold offsets into .dynstr.
        if tag not in (1, 14, 15, 29):
            continue
        string_offset = string_table + value
        if string_offset >= len(data):
            continue
        end = data.index(b"\0", string_offset)
        text = data[string_offset:end].decode("utf-8", "replace")
        if tag == 1:
            needed.append(text)
        elif tag == 14:
            soname = text
        else:
            rpath.extend(part for part in text.split(":") if part)
    return e_machine, soname, needed, rpath


def staged_libraries():
    """Map soname -> path for every staged vendor shared object."""
    found = {}
    for path in sorted((DEVICE / "recovery/root").rglob("*.so")):
        try:
            _, soname, _, _ = dynamic_entries(path)
        except ValueError:
            continue
        found[soname or path.name] = path
    return found


def check_build_config():
    board = read_make_vars(DEVICE / "BoardConfig.mk")
    for name, expected in BOARDCONFIG_EXPECTED.items():
        check(board.get(name) == expected,
              f"BoardConfig.mk: {name} is {board.get(name)!r}, expected {expected!r}")
    board_text = (DEVICE / "BoardConfig.mk").read_text(encoding="utf-8")
    check("prebuilt/kernel_empty" in board_text,
          "BoardConfig.mk: kernel-less fallback (prebuilt/kernel_empty) is missing")
    kernel = DEVICE / "prebuilt/kernel"
    if kernel.is_file():
        check(kernel.stat().st_size > 1024 * 1024,
              f"prebuilt/kernel is too small to be a kernel ({kernel.stat().st_size} bytes)")
        check("$(DEVICE_PATH)/prebuilt/kernel)" in board_text,
              "BoardConfig.mk: prebuilt/kernel exists but is not selected as TARGET_PREBUILT_KERNEL")
    else:
        check("BOARD_EXCLUDE_KERNEL_FROM_RECOVERY_IMAGE := true" in board_text,
              "BoardConfig.mk: kernel-less build must set "
              "BOARD_EXCLUDE_KERNEL_FROM_RECOVERY_IMAGE := true")

    modules = board.get("TW_LOAD_VENDOR_MODULES", "")
    for module in ("qcrypto-msm_dlkm.ko", "adsp_loader_dlkm.ko", "qti_battery_charger.ko"):
        check(module in modules, f"BoardConfig.mk: TW_LOAD_VENDOR_MODULES lacks {module}")

    product = read_make_vars(DEVICE / "twrp_nx729j.mk")
    check(product.get("PRODUCT_NAME") == "twrp_nx729j",
          "twrp_nx729j.mk: PRODUCT_NAME must be twrp_nx729j for the lunch target")
    check(product.get("PRODUCT_DEVICE") == "nx729j",
          "twrp_nx729j.mk: PRODUCT_DEVICE must be nx729j")

    products = (DEVICE / "AndroidProducts.mk").read_text(encoding="utf-8")
    check("twrp_nx729j.mk" in products, "AndroidProducts.mk does not include twrp_nx729j.mk")

    prop = (DEVICE / "system.prop").read_text(encoding="utf-8")
    check("keymaster_ver=4.x" in prop,
          "system.prop must pin keymaster_ver=4.x for the TWRP decryption path")

    flags = (DEVICE / "recovery/root/system/etc/twrp.flags").read_text(encoding="utf-8")
    check("/data" in flags, "twrp.flags does not describe /data")
    check("/metadata" in flags, "twrp.flags does not describe /metadata")


def check_fstab():
    fstab = (DEVICE / "recovery.fstab").read_text(encoding="utf-8")
    data_line = next((line for line in fstab.splitlines()
                      if "/data" in line and "fileencryption" in line), "")
    check("fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized+wrappedkey_v0" in data_line,
          "recovery.fstab: /data is missing the FBE v2 + wrappedkey descriptor")
    check("keydirectory=/metadata/vold/metadata_encryption" in data_line,
          "recovery.fstab: /data is missing keydirectory=/metadata/vold/metadata_encryption")
    check("metadata_encryption=aes-256-xts:wrappedkey_v0" in data_line,
          "recovery.fstab: /data is missing metadata_encryption")
    metadata_line = next((line for line in fstab.splitlines()
                          if "/metadata" in line and "wrappedkey" in line), "")
    check(bool(metadata_line), "recovery.fstab: /metadata must be mounted with wrappedkey")
    check("first_stage_mount" in metadata_line,
          "recovery.fstab: /metadata must be a first-stage mount")


def check_services():
    root = DEVICE / "recovery/root"
    binaries = [
        "vendor/bin/qseecomd",
        "vendor/bin/hw/android.hardware.gatekeeper@1.0-service-qti",
        "vendor/bin/hw/android.hardware.security.keymint-service-qti",
    ]
    for relative in binaries:
        path = root / relative
        if not check(path.is_file(), f"missing vendor binary: {relative}"):
            continue
        machine, _, needed, _ = dynamic_entries(path)
        check(machine == AARCH64, f"{relative} is not AArch64 (e_machine={machine})")
        check(path.stat().st_mode & 0o111, f"{relative} is not executable")

    libraries = staged_libraries()
    root_files = sorted(p for p in root.rglob("*") if p.is_file())
    for path in root_files:
        if not (path.name.endswith(".so") or path.name.startswith(("android.hardware", "lib"))):
            continue
        try:
            _, _, needed, _ = dynamic_entries(path)
        except ValueError:
            continue
        for dependency in needed:
            if dependency in libraries or dependency in BUILD_PROVIDED:
                continue
            failures.append(
                f"unresolved DT_NEEDED: {path.relative_to(root).as_posix()} -> {dependency}")

    for rc in sorted((root / "vendor/etc/init").glob("*.rc")):
        for line in rc.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("service "):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            service_binary = parts[2]
            check((root / service_binary.lstrip("/")).is_file(),
                  f"{rc.name}: service {parts[1]} points at missing {service_binary}")

    init_rc = (root / "init.recovery.qcom.rc").read_text(encoding="utf-8")
    for service in ("vendor.qseecomd", "gatekeeper-1-0", "vendor.keymint-qti"):
        check(f"start {service}" in init_rc,
              f"init.recovery.qcom.rc does not start {service}")
    check("chmod 0660 /dev/qseecom" in init_rc,
          "init.recovery.qcom.rc must grant system:drmrpc access to /dev/qseecom")


def check_vintf():
    path = DEVICE / "recovery/root/vendor/etc/vintf/manifest.xml"
    if not check(path.is_file(), "missing recovery VINTF manifest"):
        return
    try:
        tree = ET.parse(path)
    except ET.ParseError as error:
        failures.append(f"VINTF manifest is not well-formed XML: {error}")
        return
    root = tree.getroot()
    check(root.tag == "manifest", "VINTF manifest root element must be <manifest>")
    check(root.get("type") == "device", "VINTF manifest must be of type device")
    fqnames = {element.text.strip() for element in root.iter("fqname") if element.text}
    # HIDL entries spell the instance as <interface><name>/<instance>.
    for hal in root.iter("hal"):
        interface = hal.find("interface")
        if interface is None:
            continue
        name = interface.findtext("name", "").strip()
        instance = interface.findtext("instance", "").strip()
        if name and instance:
            fqnames.add(f"{name}/{instance}")
    for required in ("IKeyMintDevice/default", "IGatekeeper/default"):
        check(required in fqnames, f"VINTF manifest does not declare {required}")
    versions = [element.text.strip() for element in root.iter("version") if element.text]
    check("2" in versions, "VINTF manifest must declare KeyMint version 2 (stock A13 HAL)")


def check_modules():
    modules_dir = DEVICE / "prebuilt/nx729j/vendor/lib/modules/1.1"
    if not check(modules_dir.is_dir(), "missing prebuilt kernel modules"):
        return
    for name in ("modules.load", "modules.dep"):
        check((modules_dir / name).is_file(), f"missing {name} in prebuilt modules")
    load = (modules_dir / "modules.load").read_text(encoding="utf-8").split()
    for module in ("qcrypto-msm_dlkm.ko", "adsp_loader_dlkm.ko", "qti_battery_charger.ko"):
        check(module in load, f"{module} is not listed in modules.load")
        check((modules_dir / module).is_file(), f"{module} is listed but not staged")


def main():
    for required in ("BoardConfig.mk", "device.mk", "AndroidProducts.mk", "twrp_nx729j.mk",
                     "recovery.fstab", "recovery/root/init.recovery.qcom.rc"):
        check((DEVICE / required).is_file(), f"missing device tree file: {required}")

    if not failures:
        check_build_config()
        check_fstab()
        check_services()
        check_vintf()
        check_modules()

    report = {
        "device_tree": str(DEVICE.relative_to(REPO)),
        "checks": "build config, recovery fstab, service chain, VINTF, kernel modules",
        "status": "failed" if failures else "passed",
        "failures": failures,
    }
    print(json.dumps(report, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
