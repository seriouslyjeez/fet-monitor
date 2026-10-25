
## Fixed IP address (skip DHCP)

If the router is slow to hand out an address (the serial monitor shows "associated ... waiting for an IP address"
for a long time), open the setup page and fill in **Fixed IP address** (an unused address on the same network,
outside the router's DHCP pool, e.g. 192.168.1.60) and **Router address** (e.g. 192.168.1.1). The subnet mask
defaults to 255.255.255.0. Leave all three empty to use DHCP. With a fixed IP the target is "connected" as soon as
it joins the WiFi. Firmware 0.1.8 also waits 3 minutes (not 45 s) for DHCP before restarting a connection attempt,
since a restart only delays a slow-but-working lease.

## Slow IP address (DHCP) experiments, firmware 0.1.9

The serial monitor shows `WiFi options: ...` at start and `Connected to network: X (ip), after N s` for the first
connection. Switches in `src/platform/build_config.h`; change ONE per test and compare N over several power cycles:

| Test | FET_WIFI_NO_SLEEP | FET_WIFI_PHY_G | Also |
|------|-------------------|----------------|------|
| A (0.1.9 default) | 1 | 0 | |
| B | 0 | 0 | baseline = old behaviour |
| C | 1 | 1 | forces 802.11g |
| D | 1 | 0 | Arduino IDE Tools > lwIP Variant: other choice |

Router side: DHCP reservation for the ESP's MAC; a separate 2.4 GHz SSID with WPA2 only, protected management frames
off, fixed channel, 20 MHz; roaming assistant / Smart Connect off. A fixed IP (setup page) skips DHCP entirely.

### Firmware 0.1.10: DHCP state and fast DHCP restart

- `FET_DHCP_DIAG 1`: the "Not connected" line adds `[DHCP state S, tries T]` while associated. lwIP states:
  1 requesting (an offer was received, waiting for the ACK), 2 init, 6 selecting (discover sent, no offer yet),
  8 checking, 10 bound (has an address), 12 backing off. "Stays 6" = no offer reaches the ESP; "reaches 1 and stays" =
  the offer arrived but the final ACK did not.
- `FET_DHCP_RESTART_MS 6000`: while associated without an IP, the DHCP request is restarted every 6 s (the WiFi
  association is not dropped), because lwIP's own retry back-off grows 4, 8, 16 s. Prints `Restarting DHCP request on X`.
  Set to 0 to get the old behaviour back. Compare "Connected ... after N s" with it on and off.
- If the build fails on `lwip/netif.h` or `netif_dhcp_data`, set `FET_DHCP_DIAG 0` and tell me the error.

### Firmware 0.1.11

- The "Not connected" line now ends with `[netif <name><num> up|down dhcp STATE/TRIES; ... default=N]` for EVERY network
  interface (0.1.10 looked at only one and printed nothing). States: 1 requesting, 2 init, 6 selecting, 10 bound, 12 backing off.
- `FET_REASSOC_MS` (default 0 = off): set to 20000 to drop and re-join the WiFi when associated for 20 s without an IP
  (prints `Re-joining X`). Test it on its own, with `FET_DHCP_RESTART_MS` left at 6000, then again with it at 0.

### Firmware 0.1.12

0.1.11 hardware log: with `FET_DHCP_RESTART_MS 6000` every status line showed `st0 up dhcp 0/0` (lwIP DHCP client OFF, no
tries), so the restart through `wifi_station_dhcpc_stop/start` appears to leave the client off. It is now off by default (0).
Next: read the natural DHCP state with the restart off.

### Firmware 0.1.13

Hardware logs (0.1.9: 1267 s, 0.1.11: 1625 s to get an IP) both ended with success on the first status after a
"Retrying connection" (a fresh association), after many failed attempts. Hypothesis: each association to the ASUS
BQ16 Pro either gets DHCP answers or never does. So `FET_REASSOC_MS 15000` re-joins the WiFi after 15 s associated without
an IP (every 4th attempt waits `FET_REASSOC_LONG_MS 45000`). The restart of the DHCP request stays off
(`FET_DHCP_RESTART_MS 0`). Output: `Re-joining X (attempt N)`, then `Connected ... after N s`. Workaround, not a root-cause fix.

### Firmware 0.1.14: 802.11g by default

esp8266/Arduino issue #8299 (core > 2.5.2, ASUS routers incl. XT8 mesh: associates, no DHCP answer; the router replied
only when the ESP used 802.11g). `FET_WIFI_PHY_G` is now 1. The PHY mode can persist in the chip's flash, so after
removing it again, power-cycle and check the "WiFi options" line. Router-side alternatives from the thread: turn off
"Wi-Fi Agile Multiband" on 2.4 GHz, or disable 802.11n/"legacy" mode. The 15 s re-join (0.1.13) stays as a safety net.
