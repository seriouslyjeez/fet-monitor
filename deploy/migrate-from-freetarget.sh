#!/bin/sh
# One-time move of an older install (freetarget-server, user freetarget, /var/lib/freetarget) to the
# FeT Monitor names (fet-monitor, /opt/fet-monitor, /var/lib/fet-monitor). Run from the repository folder:
#     sudo sh deploy/migrate-from-freetarget.sh
# The old folders and unit files are left in place, disabled, so the move can be rolled back.
set -eu

OLD_SVC=freetarget-server
OLD_DATA=/var/lib/freetarget
OLD_BACKUPS=/var/backups/freetarget
NEW_USER=fet-monitor
NEW_CODE=/opt/fet-monitor
NEW_DATA=/var/lib/fet-monitor
NEW_BACKUPS=/var/backups/fet-monitor
PORT=8080

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo"
[ -d fetserver ] && [ -f deploy/fet-monitor.service ] || die "run from the repository folder (fetserver/ and deploy/ must be here)"
[ -d "$OLD_DATA" ] || die "$OLD_DATA not found: nothing to migrate (for a new install follow the README)"
if [ -e "$NEW_DATA/fetserver.db" ]; then
  die "$NEW_DATA/fetserver.db already exists: refusing to overwrite it"
fi

say "1/7 stopping the old service"
systemctl stop "$OLD_SVC" 2>/dev/null || true
systemctl stop freetarget-backup.timer freetarget-backup.service 2>/dev/null || true

say "2/7 archiving the old data (server stopped, so the copy is consistent)"
ARCHIVE="/var/backups/freetarget-before-migration-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$ARCHIVE" -C / "${OLD_DATA#/}"
say "    $ARCHIVE"

say "3/7 user and folders"
id -u "$NEW_USER" >/dev/null 2>&1 || useradd --system --home "$NEW_DATA" --shell /usr/sbin/nologin "$NEW_USER"
mkdir -p "$NEW_CODE" "$NEW_DATA" "$NEW_BACKUPS"

say "4/7 copying data, backups and code"
cp -a "$OLD_DATA/." "$NEW_DATA/"
[ -d "$OLD_BACKUPS" ] && cp -a "$OLD_BACKUPS/." "$NEW_BACKUPS/"
rm -rf "$NEW_CODE/fetserver"
cp -r fetserver "$NEW_CODE/"
chown -R "$NEW_USER:$NEW_USER" "$NEW_DATA" "$NEW_BACKUPS"

say "5/7 installing services"
cp deploy/fet-monitor.service deploy/fet-monitor-backup.service deploy/fet-monitor-backup.timer /etc/systemd/system/
if [ -d /etc/avahi/services ]; then
  cp deploy/fet-monitor.avahi.service /etc/avahi/services/fet-monitor.service
  if [ -f /etc/avahi/services/freetarget.service ]; then
    mv /etc/avahi/services/freetarget.service /etc/avahi/services/freetarget.service.disabled
  fi
fi
systemctl disable "$OLD_SVC" freetarget-backup.timer 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now fet-monitor fet-monitor-backup.timer

say "6/7 checking the new server answers"
ok=0
for i in 1 2 3 4 5 6 7 8 9 10; do
  if python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:$PORT/api/v1/health', timeout=2).status == 200 else 1)" 2>/dev/null; then
    ok=1; break
  fi
  sleep 1
done
if [ "$ok" -ne 1 ]; then
  say "The new server did not answer. Its log:"
  journalctl -u fet-monitor -n 30 --no-pager || true
  say "Roll back with:"
  say "  sudo systemctl disable --now fet-monitor fet-monitor-backup.timer"
  say "  sudo systemctl enable --now $OLD_SVC freetarget-backup.timer"
  exit 1
fi

say "7/7 done: fet-monitor is running with the old data; targets keep the same device token."
say ""
say "Roll back (the old install is untouched, only disabled):"
say "  sudo systemctl disable --now fet-monitor fet-monitor-backup.timer"
say "  sudo systemctl enable --now $OLD_SVC freetarget-backup.timer"
say "Once you are happy, remove the old install:"
say "  sudo rm /etc/systemd/system/$OLD_SVC.service /etc/systemd/system/freetarget-backup.service /etc/systemd/system/freetarget-backup.timer"
say "  sudo systemctl daemon-reload"
say "  sudo rm -rf /opt/freetarget-server $OLD_DATA $OLD_BACKUPS"
say "  sudo userdel freetarget"
say "  (keep $ARCHIVE until you no longer need it)"
