#!/bin/bash -e
# pi-gen stage on top of stage2 (Raspberry Pi OS Lite): FeT Monitor, ready for its setup network on first start.
if [ ! -d "${ROOTFS_DIR}" ]; then
	copy_previous
fi
