#!/bin/bash
# Test that a linked range rides out an internet outage (P1's gate): block only deepTENS from this Pi for a number of
# minutes, then the block lifts BY ITSELF. Targets, Range Control and the screens keep working; only the link to
# deepTENS fails (connections refused), so the deepTENS icon turns red and what is for deepTENS waits in the outbox.
#   sudo bash deeptens-outage.sh 10                 # 10 minutes (1 to 120)
#   sudo bash deeptens-outage.sh 10 deeptens.com    # another deepTENS address
#   sudo nft delete table inet fet_outage           # end it early
# Nothing is saved: a restart of the Pi ends it too.
set -euo pipefail
MIN="${1:-10}"
HOST="${2:-deeptens.com}"
[[ "$MIN" =~ ^[0-9]+$ ]] && [ "$MIN" -ge 1 ] && [ "$MIN" -le 120 ] || { echo "minutes: 1 to 120"; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "run it with sudo"; exit 1; }
command -v nft >/dev/null || { echo "nft is missing (sudo apt install nftables)"; exit 1; }
v4=$(getent ahostsv4 "$HOST" | awk '{print $1}' | sort -u | paste -sd, -)
v6=$(getent ahostsv6 "$HOST" | awk '{print $1}' | grep -v '^::ffff:' | sort -u | paste -sd, - || true)
[ -n "$v4$v6" ] || { echo "cannot find the address of $HOST"; exit 1; }
systemctl stop fet-outage-end.timer 2>/dev/null || true
nft delete table inet fet_outage 2>/dev/null || true
nft add table inet fet_outage
nft add chain inet fet_outage out '{ type filter hook output priority 0; }'
if [ -n "$v4" ]; then nft add rule inet fet_outage out ip daddr "{ $v4 }" reject; fi
if [ -n "$v6" ]; then nft add rule inet fet_outage out ip6 daddr "{ $v6 }" reject; fi
systemd-run --quiet --unit=fet-outage-end --on-active="${MIN}m" "$(command -v nft)" delete table inet fet_outage
echo "deepTENS ($HOST: ${v4}${v6:+ $v6}) is blocked from this Pi for $MIN minutes; the block lifts by itself."
echo "End it now: sudo nft delete table inet fet_outage"
