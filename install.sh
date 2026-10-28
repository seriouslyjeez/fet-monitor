#!/bin/bash
# FeT Monitor installer for Raspberry Pi OS (Bookworm or later). Installs what is in this folder; never downloads.
# Re-running it is also the upgrade path. Run from the unpacked release folder:  sudo ./install.sh
# See ./install.sh --help.
set -Eeuo pipefail      # -E: the ERR trap also fires inside functions

# ---------------------------------------------------------------------------------------------- settings
# FETM_ROOT and FETM_PORT exist only for the automated tests (a fake root folder and port); leave them unset.
R="${FETM_ROOT:-}"
PORT="${FETM_PORT:-8080}"
SVC_USER=fet-monitor
CODE_DIR="$R/opt/fet-monitor"
DATA_DIR="$R/var/lib/fet-monitor"
BACKUP_DIR="$R/var/backups/fet-monitor"
UNIT_DIR="$R/etc/systemd/system"
AVAHI_DIR="$R/etc/avahi/services"
OS_RELEASE="$R/etc/os-release"
SYSTEMD_RUN="$R/run/systemd/system"
OLD_DATA="$R/var/lib/freetarget"
SETUP_DIR="$R/var/lib/fet-setup"
UNITS=(fet-monitor.service fet-monitor-backup.service fet-monitor-backup.timer fet-monitor-update.path fet-monitor-update.service fet-setup.service fet-system.path fet-system.service fet-os-update.service fet-os-update.timer)
PACKAGES=(fetserver fetsetup)
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RESET_PASSWORD=0
NO_PASSWORD=0
UNINSTALL=0
PURGE=0
ASSUME_YES=0
IMAGE=0

# ---------------------------------------------------------------------------------------------- output
step_no=0
step() { step_no=$((step_no + 1)); printf '\n==> [%d] %s\n' "$step_no" "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\n!!  WARNING: %s\n' "$*" >&2; }
die() { printf '\nXX  ERROR: %s\n' "$*" >&2; exit 1; }
on_error() { printf '\nXX  ERROR: step %d failed (line %s, exit code %s). Nothing after it was done.\n' "$step_no" "$1" "$2" >&2; }
trap 'on_error "$LINENO" "$?"' ERR

usage() {
  cat <<'EOF'
FeT Monitor installer (Raspberry Pi OS Bookworm or later)

Usage:  sudo ./install.sh [options]          run from the unpacked release folder

Installs or upgrades FeT Monitor from THIS folder (it never downloads anything):
  code in /opt/fet-monitor, database in /var/lib/fet-monitor, backups in /var/backups/fet-monitor,
  systemd units fet-monitor.service and fet-monitor-backup.timer, system user fet-monitor.
Safe to re-run: a re-run upgrades the code and restarts the service. The database is never touched,
except for the one-time move of an older "freetarget" install, which is backed up first.

Options:
  --reset-password   ask for a new Range Control password even if one is set
  --no-password      do not ask for a password now (set it later with the command printed at the end)
  --uninstall        stop and remove the service and code; KEEPS the database, backups and device token
  --purge            with --uninstall: also delete the database, backups, device token and user (asks first)
  --yes              with --purge: do not ask for confirmation
  --image            building a FeT Monitor SD card image (in a chroot): no password, nothing started; the first
                     boot opens the setup network
  -h, --help         this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --reset-password) RESET_PASSWORD=1 ;;
    --no-password) NO_PASSWORD=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --purge) PURGE=1 ;;
    --yes) ASSUME_YES=1 ;;
    --image) IMAGE=1; NO_PASSWORD=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
  shift
done
[ "$PURGE" -eq 0 ] || [ "$UNINSTALL" -eq 1 ] || die "--purge only goes with --uninstall"
[ "$RESET_PASSWORD" -eq 0 ] || [ "$NO_PASSWORD" -eq 0 ] || die "--reset-password and --no-password contradict each other"

# ---------------------------------------------------------------------------------------------- helpers
version_of_release() { sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$HERE/fetserver/__init__.py"; }

health() {   # 0 when the server answers /api/v1/health with ok
  python3 - "$PORT" <<'EOF' >/dev/null 2>&1
import json, sys, urllib.request
with urllib.request.urlopen("http://127.0.0.1:%s/api/v1/health" % sys.argv[1], timeout=2) as r:
    sys.exit(0 if r.status == 200 and json.load(r).get("ok") else 1)
EOF
}

wait_healthy() {   # a Pi Zero 2 W can take a while to start Python
  local tries=$1
  while [ "$tries" -gt 0 ]; do
    if health; then return 0; fi
    sleep 1
    tries=$((tries - 1))
  done
  return 1
}

primary_ip() {
  local ip=""
  if command -v ip >/dev/null 2>&1; then
    ip="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n 1)" || true
  fi
  if [ -z "$ip" ] && command -v hostname >/dev/null 2>&1; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')" || true
  fi
  printf '%s' "${ip:-<the Pi IP address>}"
}

as_service_user() { runuser -u "$SVC_USER" -- "$@"; }

set_password_cmd="cd /opt/fet-monitor && sudo -u $SVC_USER python3 -m fetserver set-password --data-dir /var/lib/fet-monitor"

# ---------------------------------------------------------------------------------------------- preflight
preflight() {
  step "Checking this system"
  [ "$(id -u)" -eq 0 ] || die "run with sudo:  sudo ./install.sh"
  [ -f "$HERE/fetserver/__init__.py" ] && [ -f "$HERE/deploy/fet-monitor.service" ] \
    || die "fetserver/ and deploy/ were not found next to install.sh; run it from the unpacked release folder"

  [ -r "$OS_RELEASE" ] || die "cannot read /etc/os-release; this installer is for Raspberry Pi OS / Debian"
  local id like ver code
  # shellcheck source=/dev/null
  id="$(. "$OS_RELEASE" && printf '%s' "${ID:-}")"
  # shellcheck source=/dev/null
  like="$(. "$OS_RELEASE" && printf '%s' "${ID_LIKE:-}")"
  # shellcheck source=/dev/null
  ver="$(. "$OS_RELEASE" && printf '%s' "${VERSION_ID:-0}")"
  # shellcheck source=/dev/null
  code="$(. "$OS_RELEASE" && printf '%s' "${VERSION_CODENAME:-}")"
  case "$id" in                        # Debian derivatives (ID_LIKE=debian) number their versions differently
    debian|raspbian) ;;
    *) die "this is $id${like:+ (like $like)}, not Raspberry Pi OS / Debian" ;;
  esac
  [[ "$ver" =~ ^[0-9]+$ ]] || die "could not read the Debian version from /etc/os-release (VERSION_ID=$ver)"
  [ "$ver" -ge 12 ] || die "Debian $ver ($code) is too old: Raspberry Pi OS Bookworm (Debian 12) or later is needed"
  if [ "$ver" -gt 12 ]; then
    warn "Debian $ver ($code) has not been tested with FeT Monitor (tested target: Bookworm). Continuing."
  fi
  info "OS: Debian $ver ($code)"

  command -v python3 >/dev/null 2>&1 || die "python3 is not installed (sudo apt install python3)"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "Python 3.9 or newer is needed; found $(python3 -V 2>&1)"
  info "$(python3 -V 2>&1)"

  if [ "$IMAGE" -eq 1 ]; then
    command -v systemctl >/dev/null 2>&1 || die "systemctl is missing"
  else
    command -v systemctl >/dev/null 2>&1 && [ -d "$SYSTEMD_RUN" ] || die "systemd is not running on this system"
  fi
  command -v runuser >/dev/null 2>&1 || die "runuser (util-linux) is missing"
  info "systemd: ok"
  if ! command -v nmcli >/dev/null 2>&1; then
    warn "NetworkManager (nmcli) is missing: the setup network and page will not run (Raspberry Pi OS has it)"
  fi
  if ! command -v ssh-keygen >/dev/null 2>&1; then
    warn "ssh-keygen is missing: updates from Range Control cannot check release signatures (sudo apt install openssh-client)"
  fi
}

# ---------------------------------------------------------------------------------------------- uninstall
uninstall() {
  step "Stopping and removing the service"
  systemctl disable --now fet-monitor.service fet-monitor-backup.timer fet-monitor-update.path fet-setup.service fet-system.path fet-os-update.timer >/dev/null 2>&1 || true
  local u
  for u in "${UNITS[@]}"; do rm -f "$UNIT_DIR/$u"; done
  rm -f "$AVAHI_DIR/fet-monitor.service"
  systemctl daemon-reload
  step "Removing the code"
  rm -rf "$CODE_DIR"
  if [ "$PURGE" -eq 1 ]; then
    if [ "$ASSUME_YES" -ne 1 ]; then
      printf '\nThis deletes ALL shots, the device token and every backup in %s and %s.\n' "$DATA_DIR" "$BACKUP_DIR"
      local answer=""
      read -r -p 'Type DELETE to confirm: ' answer || true
      [ "$answer" = "DELETE" ] || { info "Not confirmed: database and backups kept."; PURGE=0; }
    fi
  fi
  if [ "$PURGE" -eq 1 ]; then
    step "Deleting the database, backups and user"
    rm -rf "$DATA_DIR" "$BACKUP_DIR" "$SETUP_DIR"
    if id -u "$SVC_USER" >/dev/null 2>&1; then userdel "$SVC_USER"; fi
    printf '\nFeT Monitor removed completely.\n'
  else
    printf '\nFeT Monitor removed. Kept: %s (database, device token, password) and %s (backups).\n' "$DATA_DIR" "$BACKUP_DIR"
    printf 'Re-running install.sh brings it back with all data. To delete them: sudo ./install.sh --uninstall --purge\n'
  fi
}

# ---------------------------------------------------------------------------------------------- install steps
migrate_old_install() {
  if [ -d "$OLD_DATA" ] && [ ! -e "$DATA_DIR/fetserver.db" ]; then
    step "Found an older install (freetarget): backing it up, then moving it"
    mkdir -p "$R/var/backups"
    local archive
    archive="$R/var/backups/freetarget-full-before-install-$(date +%Y%m%d-%H%M%S).tar.gz"
    systemctl stop freetarget-server >/dev/null 2>&1 || true      # a consistent copy of the database
    local parts=(var/lib/freetarget)
    local p
    for p in var/backups/freetarget opt/freetarget-server etc/systemd/system/freetarget-server.service \
             etc/systemd/system/freetarget-backup.service etc/systemd/system/freetarget-backup.timer; do
      if [ -e "$R/$p" ]; then parts+=("$p"); fi
    done
    tar -czf "$archive" -C "$R/" "${parts[@]}"
    info "backup: $archive"
    (cd "$HERE" && sh deploy/migrate-from-freetarget.sh)
    info "old install moved; its folders are kept (disabled) for rollback, see the lines above"
  elif [ -d "$OLD_DATA" ]; then
    info "an old /var/lib/freetarget is still present but already migrated; leaving it alone"
  fi
}

create_user_and_dirs() {
  step "User and folders"
  if id -u "$SVC_USER" >/dev/null 2>&1; then
    info "user $SVC_USER exists"
  else
    useradd --system --no-create-home --home-dir /var/lib/fet-monitor --shell /usr/sbin/nologin "$SVC_USER"
    info "created system user $SVC_USER (no login)"
  fi
  mkdir -p "$CODE_DIR" "$DATA_DIR" "$BACKUP_DIR" "$DATA_DIR/update" "$DATA_DIR/system"
  chown root:root "$CODE_DIR"
  chmod 0755 "$CODE_DIR"
  chown "$SVC_USER:$SVC_USER" "$DATA_DIR" "$BACKUP_DIR" "$DATA_DIR/update" "$DATA_DIR/system"
  chmod 0750 "$DATA_DIR" "$BACKUP_DIR" "$DATA_DIR/update" "$DATA_DIR/system"
  info "$CODE_DIR (root, 755)  $DATA_DIR and $BACKUP_DIR ($SVC_USER, 750)"
}

install_code() {
  step "Installing the code ($(version_of_release))"
  local was_active=0
  if systemctl is-active --quiet fet-monitor.service 2>/dev/null; then
    was_active=1
    info "stopping the running server for the upgrade"
    systemctl stop fet-monitor.service
  fi
  local pkg
  for pkg in "${PACKAGES[@]}"; do              # each package swapped in whole: never half old, half new
    rm -rf "$CODE_DIR/$pkg.new" "$CODE_DIR/$pkg.old"
    cp -R "$HERE/$pkg" "$CODE_DIR/$pkg.new"
    find "$CODE_DIR/$pkg.new" -name '__pycache__' -type d -prune -exec rm -rf {} +
    chown -R root:root "$CODE_DIR/$pkg.new"
    chmod -R u=rwX,go=rX "$CODE_DIR/$pkg.new"
    if [ -d "$CODE_DIR/$pkg" ]; then mv "$CODE_DIR/$pkg" "$CODE_DIR/$pkg.old"; fi
    mv "$CODE_DIR/$pkg.new" "$CODE_DIR/$pkg"
    rm -rf "$CODE_DIR/$pkg.old"
  done
  mkdir -p "$CODE_DIR/bin"                     # the root update helper: root-owned, outside anything the server can write
  install -m 0755 "$HERE/deploy/fet-monitor-update.sh" "$CODE_DIR/bin/fet-monitor-update"
  chown root:root "$CODE_DIR/bin" "$CODE_DIR/bin/fet-monitor-update"
  local f
  for f in LICENSE NOTICE; do
    if [ -f "$HERE/$f" ]; then install -m 0644 "$HERE/$f" "$CODE_DIR/$f"; fi
  done
  info "code in $CODE_DIR/fetserver and $CODE_DIR/fetsetup"
  [ "$was_active" -eq 0 ] || info "(the server is restarted below)"
}

install_units() {
  step "systemd units"
  mkdir -p "$UNIT_DIR"
  local u
  for u in "${UNITS[@]}"; do install -m 0644 "$HERE/deploy/$u" "$UNIT_DIR/$u"; done
  if [ -d "$AVAHI_DIR" ]; then install -m 0644 "$HERE/deploy/fet-monitor.avahi.service" "$AVAHI_DIR/fet-monitor.service"; fi
  [ "$IMAGE" -eq 1 ] || systemctl daemon-reload            # a chroot has no running systemd; enable still works
  systemctl enable fet-monitor.service fet-monitor-backup.timer fet-monitor-update.path fet-setup.service fet-system.path fet-os-update.timer >/dev/null 2>&1
  info "installed and enabled: fet-monitor.service, fet-monitor-backup.timer (nightly backup),"
  info "fet-monitor-update.path (installs signed updates requested from Range Control),"
  info "fet-setup.service (setup network on first start, or when the Pi finds no network),"
  info "fet-system.path (applies Range Control's System page: network, name, time, SSH, restart),"
  info "fet-os-update.timer (nightly Raspberry Pi OS update when the range is quiet; restarts if needed)"
}

device_token() {
  step "Device token"
  local tok="$DATA_DIR/device_token"
  if [ "$IMAGE" -eq 1 ]; then                  # never one token for every Pi made from the image
    rm -f "$tok"
    info "image: each Pi creates its own token when FeT Monitor first starts"
    return 0
  fi
  if [ -s "$tok" ]; then
    info "kept the existing token (targets keep working)"
  else
    (umask 077 && python3 -c 'import secrets; print(secrets.token_urlsafe(18))' > "$tok.tmp")
    mv "$tok.tmp" "$tok"
    info "created a new token"
  fi
  chown "$SVC_USER:$SVC_USER" "$tok"
  chmod 0600 "$tok"
}

setup_marker() {
  step "First-time setup"
  mkdir -p "$SETUP_DIR"
  chmod 0755 "$SETUP_DIR"
  if [ -e "$SETUP_DIR/setup.done" ]; then
    info "already set up"
  elif [ "$IMAGE" -eq 1 ]; then
    info "image: the first start opens the setup network FeTMonitor-Setup-XXXX"
  elif ip -4 route get 1.1.1.1 >/dev/null 2>&1; then          # installed over a working network: no setup network
    printf '{"by": "install.sh"}\n' > "$SETUP_DIR/setup.done"
    info "this Pi has a network: no setup network (it opens only if the Pi starts without a network)"
  else
    info "no network now: the next start opens the setup network FeTMonitor-Setup-XXXX"
  fi
}

range_password() {
  step "Range Control password"
  local pw="$DATA_DIR/admin_password"
  if [ "$NO_PASSWORD" -eq 1 ]; then
    info "skipped (--no-password). Set it later with:"
    info "  $set_password_cmd"
    return 0
  fi
  if [ -s "$pw" ] && [ "$RESET_PASSWORD" -eq 0 ]; then
    info "already set (kept). To change it: sudo ./install.sh --reset-password"
    return 0
  fi
  local tries=3
  while [ "$tries" -gt 0 ]; do
    if (cd "$CODE_DIR" && as_service_user python3 -m fetserver set-password --data-dir "$DATA_DIR"); then
      return 0
    fi
    tries=$((tries - 1))
    [ "$tries" -eq 0 ] || info "try again ($tries left)"
  done
  warn "no Range Control password was set. Set it later with:  $set_password_cmd"
}

start_and_check() {
  step "Starting the server"
  systemctl restart fet-monitor.service
  systemctl start fet-monitor-backup.timer fet-monitor-update.path
  local wait_s="${FETM_HEALTH_WAIT:-45}"         # FETM_HEALTH_WAIT: tests only
  if wait_healthy "$wait_s"; then
    info "running and answering on port $PORT"
  else
    printf '\n' >&2
    journalctl -u fet-monitor.service -n 30 --no-pager >&2 || true
    die "the server did not answer on port $PORT within $wait_s s (log above). Check: systemctl status fet-monitor"
  fi
}

summary() {
  local ip tok
  ip="$(primary_ip)"
  tok="$(cat "$DATA_DIR/device_token")"
  cat <<EOF

=====================================================================
 FeT Monitor $(version_of_release) is installed and running.

 Open in a browser on the same network:   http://$ip:$PORT/

 Device token (enter it in each target's setup portal, with the
 server address $ip):

     $tok

 Next steps
  1. Give the Pi a fixed address: a DHCP reservation in the router for
     $ip (targets need the IP address; .local names did not work).
  2. Set up each target: open its setup portal (switch the target off
     and on three times quickly, join the "FeTMonitor-..." WiFi) and
     enter the club WiFi, server address $ip and the token above.
  3. Range Control password: change it any time with
       sudo ./install.sh --reset-password
  4. Status and logs:  systemctl status fet-monitor
                       journalctl -u fet-monitor -f
=====================================================================
EOF
}

# ---------------------------------------------------------------------------------------------- main
preflight
if [ "$UNINSTALL" -eq 1 ]; then
  uninstall
  exit 0
fi
migrate_old_install
create_user_and_dirs
install_code
install_units
device_token
setup_marker
range_password
if [ "$IMAGE" -eq 1 ]; then
  printf '\nFeT Monitor %s installed in the image. The first start opens the setup network.\n' "$(version_of_release)"
  exit 0
fi
start_and_check
summary
