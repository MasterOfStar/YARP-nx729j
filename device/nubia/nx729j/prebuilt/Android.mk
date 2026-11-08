LOCAL_PATH := $(call my-dir)

# Stage the stock vendor_dlkm module tree and the touch/display firmware into
# the recovery ramdisk. modules.dep / modules.alias ship with the stock tree,
# so depmod only runs when the host tool is actually available.
include $(CLEAR_VARS)
    LOCAL_MODULE := nx729j_prebuilt
    LOCAL_MODULE_TAGS := optional
    LOCAL_MODULE_CLASS := ETC
    LOCAL_MODULE_PATH := $(TARGET_RECOVERY_ROOT_OUT)
    LOCAL_HOST_REQUIRED_MODULES := depmod
    LOCAL_POST_INSTALL_CMD += \
        mkdir -p $(TARGET_RECOVERY_ROOT_OUT)/vendor; \
        cp -rf $(LOCAL_PATH)/$(PRODUCT_RELEASE_NAME)/vendor $(TARGET_RECOVERY_ROOT_OUT)/; \
        if [ -n "$(DEPMOD)" ] && [ -x "$(DEPMOD)" ]; then \
            echo "calling depmod on nx729j_prebuilt"; \
            $(DEPMOD) -b $(TARGET_RECOVERY_ROOT_OUT)/vendor 1.1; \
        else \
            echo "depmod unavailable; keeping the stock modules.dep"; \
        fi
include $(BUILD_PHONY_PACKAGE)
