#!/bin/bash -e
# Inside the image: install FeT Monitor without starting anything (no systemd here), SSH off (the setup page or
# Range Control turns it on). The first start opens the setup network FeTMonitor-Setup-XXXX.
cd /tmp/fet-monitor
bash ./install.sh --image
cd /
rm -rf /tmp/fet-monitor
systemctl disable ssh.service >/dev/null 2>&1 || true
# pi-gen's build user (the workflow's username) had a throwaway password: locked, expired, no shell
if id fetimage >/dev/null 2>&1; then
  usermod --lock --expiredate 1 --shell /usr/sbin/nologin fetimage
fi
