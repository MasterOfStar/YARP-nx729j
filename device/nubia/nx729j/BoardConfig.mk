#
# Copyright (C) 2023 The Android Open Source Project
#
# SPDX-License-Identifier: Apache-2.0
#

DEVICE_PATH := device/nubia/nx729j

# Building with minimal manifest
ALLOW_MISSING_DEPENDENCIES := true

# Architecture
TARGET_ARCH := arm64
TARGET_ARCH_VARIANT := armv8-a
TARGET_CPU_ABI := arm64-v8a
TARGET_CPU_ABI2 :=
TARGET_CPU_VARIANT := kryo
TARGET_CPU_VARIANT_RUNTIME := kryo300

TARGET_2ND_ARCH := arm
TARGET_2ND_ARCH_VARIANT := armv8-a
TARGET_2ND_CPU_ABI := armeabi-v7a
TARGET_2ND_CPU_ABI2 := armeabi
TARGET_2ND_CPU_VARIANT := $(TARGET_CPU_VARIANT)

# Power
ENABLE_CPUSETS := true
ENABLE_SCHEDBOOST := true

# Bootloader
PRODUCT_PLATFORM := kalama
TARGET_BOOTLOADER_BOARD_NAME := nx729j
TARGET_NO_BOOTLOADER := true
TARGET_USES_UEFI := true

# Platform
TARGET_BOARD_PLATFORM := nubia_sm8550
TARGET_BOARD_PLATFORM_GPU := qcom-adreno740
QCOM_BOARD_PLATFORMS += nubia_sm8550

# Kernel
BOARD_KERNEL_PAGESIZE         := 4096
TARGET_KERNEL_ARCH            := arm64
TARGET_KERNEL_HEADER_ARCH     := arm64
BOARD_KERNEL_IMAGE_NAME       := Image
BOARD_BOOT_HEADER_VERSION     := 4
TARGET_KERNEL_CLANG_COMPILE   := true
BOARD_MKBOOTIMG_ARGS          += --header_version $(BOARD_BOOT_HEADER_VERSION)
BOARD_MKBOOTIMG_ARGS          += --pagesize $(BOARD_KERNEL_PAGESIZE)

# The recovery partition holds a ramdisk only; the bootloader takes the kernel
# from boot.img. Verified against this device's stock recovery.img header
# (/dev/block/by-name/recovery_a, first 4096 bytes):
#   header_version 4, header_size 1584, cmdline empty,
#   kernel_size = 0, ramdisk_size = 56129408
# boot.img itself is kernel only (ramdisk_size = 0), and the stock
# vendor/etc/fstab.qcom declares "/recovery emmc slotselect,avb=vbmeta", so
# recovery is a standalone partition that never carries a kernel of its own.
#
# The AOSP build still wants a kernel on disk for its VINTF kernel checks
# (PRODUCT_OTA_ENFORCE_VINTF_KERNEL_REQUIREMENTS runs extract_kernel.py over
# $(PRODUCT_OUT)/kernel), so stage the stock image's kernel when it is present.
# BOARD_EXCLUDE_KERNEL_FROM_RECOVERY_IMAGE keeps it out of recovery.img either
# way, which is the part that matters on hardware.
ifneq ($(wildcard $(DEVICE_PATH)/prebuilt/kernel),)
TARGET_PREBUILT_KERNEL := $(DEVICE_PATH)/prebuilt/kernel
else
TARGET_PREBUILT_KERNEL := $(DEVICE_PATH)/prebuilt/kernel_empty
endif
BOARD_EXCLUDE_KERNEL_FROM_RECOVERY_IMAGE := true

# Ramdisk
BOARD_RAMDISK_USE_LZ4 := true

# A/B
AB_OTA_UPDATER := true
AB_OTA_PARTITIONS += \
    boot \
    init_boot \
    vendor_boot \
    dtbo \
    vbmeta \
    vbmeta_system \
    odm \
    product \
    system \
    system_ext \
    system_dlkm \
    vendor \
    vendor_dlkm

# Verified Boot
BOARD_AVB_ENABLE := true

# Partitions
BOARD_RECOVERYIMAGE_PARTITION_SIZE := 104857600
BOARD_FLASH_BLOCK_SIZE := 262144

# Dynamic Partitions
BOARD_SUPER_PARTITION_SIZE := 9653190656
BOARD_SUPER_PARTITION_GROUPS := qti_dynamic_partitions
BOARD_QTI_DYNAMIC_PARTITIONS_SIZE := 9648996352
BOARD_QTI_DYNAMIC_PARTITIONS_PARTITION_LIST := system system_ext product vendor vendor_dlkm odm

# Every dynamic partition in the super partition is ext4 on this device.
BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_SYSTEM_EXTIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_PRODUCTIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_VENDORIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_VENDOR_DLKMIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_ODMIMAGE_FILE_SYSTEM_TYPE := ext4

TARGET_COPY_OUT_SYSTEM := system
TARGET_COPY_OUT_SYSTEM_EXT := system_ext
TARGET_COPY_OUT_PRODUCT := product
TARGET_COPY_OUT_VENDOR := vendor
TARGET_COPY_OUT_VENDOR_DLKM := vendor_dlkm
TARGET_COPY_OUT_ODM := odm

BOARD_USERDATAIMAGE_FILE_SYSTEM_TYPE := f2fs

# File systems
TARGET_USERIMAGES_USE_EXT4 := true
TARGET_USERIMAGES_USE_F2FS := true
BOARD_USES_VENDOR_DLKMIMAGE := true

# Recovery
BOARD_HAS_LARGE_FILESYSTEM := true
TARGET_RECOVERY_PIXEL_FORMAT := RGBX_8888
TARGET_RECOVERY_FSTAB := $(DEVICE_PATH)/recovery.fstab
TARGET_RECOVERY_QCOM_RTC_FIX := true
TARGET_USES_MKE2FS := true

# ---------------------------------------------------------------------------
# Crypto / /data decryption
#
# Decryption chain implemented by YARP (twrp-16.0):
#   TWRP -> libvold (static) -> keystore2 (built into the recovery ramdisk,
#   talks AIDL android.system.keystore2) -> vendor KeyMint HAL -> qseecomd -> TEE
#
# NX729J ships Android 13 with a QTI AIDL KeyMint 2.0 HAL
# (vendor/bin/hw/android.hardware.security.keymint-service-qti) plus a legacy
# HIDL gatekeeper 1.0 HAL; both are staged in recovery/root and are supported
# by twrp-16.0's vold (Decrypt.cpp falls back to HIDL gatekeeper when the AIDL
# instance is not declared).
# ---------------------------------------------------------------------------
TW_INCLUDE_CRYPTO := true
TW_INCLUDE_CRYPTO_FBE := true
TW_INCLUDE_FBE_METADATA_DECRYPT := true
BOARD_USES_METADATA_PARTITION := true
TW_USE_FSCRYPT_POLICY := 2
BOARD_USES_QCOM_FBE_DECRYPTION := false

# Platform version / security patch
# PLATFORM_VERSION feeds os_version in the boot image header:
#   os_version = (A << 25) | (B << 18) | (C << 11) | (year << 4) | month
# The stock recovery.img reports 436209212 = 0x1A00063C, which is
# A=13, patch 2099-12: exactly PLATFORM_VERSION 13 with the 2099-12-31 hack
# below. The usual TWRP value 99.87.36 encodes A=99, i.e. 0xC600063C, which is
# negative as a signed 32-bit int and does not match this bootloader's
# expectations, so this tree stays at 13.
PLATFORM_VERSION := 13
PLATFORM_VERSION_LAST_STABLE := $(PLATFORM_VERSION)
PLATFORM_SECURITY_PATCH := 2099-12-31
VENDOR_SECURITY_PATCH := $(PLATFORM_SECURITY_PATCH)
BOOT_SECURITY_PATCH := $(PLATFORM_SECURITY_PATCH)

# Tools
TW_INCLUDE_REPACKTOOLS := true
TW_INCLUDE_RESETPROP := true
TW_INCLUDE_LIBRESETPROP := true
TW_INCLUDE_7ZA := true
TW_INCLUDE_ZSTD := true
TW_ENABLE_ALL_PARTITION_TOOLS := true

# Debug
TARGET_USES_LOGD := true
TWRP_INCLUDE_LOGCAT := true
TARGET_RECOVERY_DEVICE_MODULES += debuggerd strace

# Fastbootd
TW_INCLUDE_FASTBOOTD := true

# Other TWRP configuration
TW_THEME := portrait_hdpi
TW_FRAMERATE := 60
RECOVERY_SDCARD_ON_DATA := true
TW_EXCLUDE_DEFAULT_USB_INIT := true
TW_INCLUDE_NTFS_3G := true
TW_USE_TOOLBOX := true
TW_INPUT_BLACKLIST := "hbtp_vm"
TW_BRIGHTNESS_PATH := "/sys/class/backlight/panel0-backlight/brightness"
TW_MAX_BRIGHTNESS := 2047
TW_EXTRA_LANGUAGES := true
TW_DEFAULT_LANGUAGE := zh_CN
TW_DEFAULT_BRIGHTNESS := 200
TW_EXCLUDE_APEX := true
TW_HAS_EDL_MODE := true
TW_USE_SERIALNO_PROPERTY_FOR_DEVICE_ID := true
TW_CUSTOM_CPU_TEMP_PATH := "/sys/class/thermal/thermal_zone35/temp"
TW_BACKUP_EXCLUSIONS := /data/fonts
TW_DEVICE_VERSION := YARP-NX729J-A13
TW_NO_HAPTICS := true
TW_SUPPORT_INPUT_AIDL_HAPTICS := false
TW_SUPPORT_INPUT_AIDL_HAPTICS_FQNAME := "IVibrator/vibratorfeature"
TW_SUPPORT_INPUT_AIDL_HAPTICS_FIX_OFF := true

# Vendor kernel modules loaded at boot.
# Keep this identical to the known-good TWRP for this device (adsp_loader plus
# the charger), which is what boots reliably here. qcrypto-msm_dlkm (with its
# qce50_dlkm dependency) was dropped: it is the QCE crypto engine, not the
# inline-crypto engine, and it is the only difference in the load set against
# the TWRP build that is known to boot on this hardware. FBE uses blk-crypto,
# whose software fallback is built into this kernel (blk_crypto_fallback_*),
# so /data decryption does not depend on QCE.
TW_LOAD_VENDOR_MODULES := "adsp_loader_dlkm.ko qti_battery_charger.ko"
# The prebuilt set is the stock vendor_dlkm module tree; this ramdisk has no
# separate *-gki module directory, so skip probing for one.
TW_LOAD_VENDOR_MODULES_EXCLUDE_GKI := true

# Broken rules
BUILD_BROKEN_DUP_RULES := true
BUILD_BROKEN_ELF_PREBUILT_PRODUCT_COPY_FILES := true
