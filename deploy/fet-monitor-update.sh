#!/bin/bash
# FeT Monitor update helper. Runs as root, started by fet-monitor-update.path when the server (which runs as the
# unprivileged fet-monitor user) asks for an update by writing /var/lib/fet-monitor/update/request.json next to the
# downloaded release in .../update/staging/. Everything handed over is treated as untrusted and checked again here:
# the signature with the INSTALLED signing key, the archive checksum, the archive layout and version. Then: database
# backup, keep the old code, run the new release's install.sh; if the new version does not come up, put the old code
# and the database back.
set -Eeuo pipefail

# FETM_ROOT (and FETM_PORT, read by install.sh) exist only for the automated tests; leave them unset.
R="${FETM_ROOT:-}"
SVC_USER=fet-monitor
CODE="$R/opt/fet-monitor"
DATA="$R/var/lib/fet-monitor"
UPD="$DATA/update"
REQ="$UPD/request.json"
STAGE="$UPD/staging"
STATUS="$UPD/status.json"
LOG="$UPD/last-update.log"
BACKUPS="$R/var/backups/fet-monitor/pre-update"
SIGNERS="$CODE/fetserver/release_signers"

status() {   # state, version, message: a small JSON file the server shows in Range Control
  python3 - "$STATUS" "$1" "$2" "$3" <<'EOF'
import json, os, sys, time
path, state, version, msg = sys.argv[1:5]
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump({"state": state, "version": version, "message": msg[-2000:], "time": int(time.time() * 1000)}, f)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
EOF
}

[ -f "$REQ" ] || exit 0
VERSION="$(python3 - "$REQ" <<'EOF' || true
import json, re, sys
try:
    v = json.load(open(sys.argv[1])).get("version", "")
except Exception:
    v = ""
print(v if isinstance(v, str) and re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}", v) else "")
EOF
)"
rm -f "$REQ"                                           # one attempt per request (the path unit fires on its existence)
if [ -z "$VERSION" ]; then status failed "" "bad update request"; exit 1; fi
status running "$VERSION" "checking the release"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail() { status failed "$VERSION" "$1"; printf 'update %s failed: %s\n' "$VERSION" "$1" >&2; exit 1; }

ARCHIVE="fet-monitor-$VERSION.tar.gz"
for f in "$ARCHIVE" SHA256SUMS SHA256SUMS.sig; do
  [ -f "$STAGE/$f" ] && [ ! -L "$STAGE/$f" ] || fail "staged file $f is missing or not a plain file"
  cat "$STAGE/$f" > "$WORK/$f"                         # our own copies: nothing can change them after the checks
done
[ -f "$SIGNERS" ] || fail "no release signing key installed"
ssh-keygen -Y verify -f "$SIGNERS" -I release@fet-monitor -n fet-monitor-release -s "$WORK/SHA256SUMS.sig" \
  < "$WORK/SHA256SUMS" >/dev/null 2>&1 || fail "the release signature is NOT valid"
(cd "$CODE" && python3 - "$WORK" "$ARCHIVE" "$VERSION" <<'EOF') || fail "the archive did not pass its checks"
import hashlib, sys
from pathlib import Path
from fetserver.releases import ReleaseError, check_archive, parse_sums
work, name, version = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
try:
    sums = parse_sums((work / "SHA256SUMS").read_bytes())
    data = (work / name).read_bytes()
    if sums.get(name) != hashlib.sha256(data).hexdigest():
        raise ReleaseError("checksum mismatch")
    check_archive(data, version)
except ReleaseError as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
EOF
tar -xzf "$WORK/$ARCHIVE" --no-same-owner -C "$WORK"
NEW="$WORK/fet-monitor-$VERSION"
[ -f "$NEW/install.sh" ] || fail "install.sh missing after unpacking"

status running "$VERSION" "backing up the database"
mkdir -p "$BACKUPS"
chown "$SVC_USER:$SVC_USER" "$BACKUPS"
chmod 0750 "$BACKUPS"
(cd "$CODE" && runuser -u "$SVC_USER" -- python3 -m fetserver backup --data-dir "$DATA" --to "$BACKUPS" --keep 5) >>"$LOG" 2>&1 \
  || fail "the database backup failed; nothing was changed"
backups=("$BACKUPS"/fet-*.db)                          # named fet-YYYYmmdd-HHMMSS.db: the last one is the newest
DB_BACKUP="${backups[${#backups[@]}-1]}"
[ -f "$DB_BACKUP" ] || fail "the database backup is missing; nothing was changed"

status running "$VERSION" "installing"
PKGS=(fetserver fetsetup)                               # every package the install replaces; .prev for a rollback
for pkg in "${PKGS[@]}"; do
  rm -rf "$CODE/$pkg.prev"
  if [ -d "$CODE/$pkg" ]; then cp -a "$CODE/$pkg" "$CODE/$pkg.prev"; fi
done
if (cd "$NEW" && bash ./install.sh --no-password) >>"$LOG" 2>&1; then
  status "done" "$VERSION" "updated to $VERSION (database backup: $DB_BACKUP)"
  exit 0
fi

# The new version did not come up: back to the old code and the database as it was before the update (the new
# version may have upgraded its structure, which the old code would refuse).
status running "$VERSION" "update failed, rolling back"
systemctl stop fet-monitor.service || true
for pkg in "${PKGS[@]}"; do                            # the previous code; a package it did not have is removed
  rm -rf "$CODE/$pkg"
  if [ -d "$CODE/$pkg.prev" ]; then mv "$CODE/$pkg.prev" "$CODE/$pkg"; fi
done
cp "$DB_BACKUP" "$DATA/fetserver.db.restore"
rm -f "$DATA/fetserver.db-wal" "$DATA/fetserver.db-shm"
mv "$DATA/fetserver.db.restore" "$DATA/fetserver.db"
chown "$SVC_USER:$SVC_USER" "$DATA/fetserver.db"
systemctl start fet-monitor.service || true
back=no
for _ in $(seq 1 30); do                               # is the previous version answering again?
  if python3 - "${FETM_PORT:-8080}" <<'EOF' >/dev/null 2>&1
import json, sys, urllib.request
with urllib.request.urlopen("http://127.0.0.1:%s/api/v1/health" % sys.argv[1], timeout=2) as r:
    sys.exit(0 if json.load(r).get("ok") else 1)
EOF
  then back=yes; break; fi
  sleep 1
done
tail_log="$(tail -n 12 "$LOG" 2>/dev/null | tr '\n' ' ')"
if [ "$back" = yes ]; then
  status rolled_back "$VERSION" "the new version did not start; the previous version and its database were restored and it is running. Log: $tail_log"
else
  status failed "$VERSION" "the new version did not start AND the restored previous version is not answering: check systemctl status fet-monitor. Log: $tail_log"
fi
exit 1
