#!/bin/bash -e
# The unpacked FeT Monitor release (the workflow puts it in files/fet-monitor) into the image, for 02-run-chroot.sh.
[ -f files/fet-monitor/install.sh ] || { echo "files/fet-monitor is missing: build the release first"; exit 1; }
install -d "${ROOTFS_DIR}/tmp/fet-monitor"
cp -a files/fet-monitor/. "${ROOTFS_DIR}/tmp/fet-monitor/"
