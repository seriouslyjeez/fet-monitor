
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
