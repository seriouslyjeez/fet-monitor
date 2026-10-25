
## Fixed IP address (skip DHCP)

If the router is slow to hand out an address (the serial monitor shows "associated ... waiting for an IP address"
for a long time), open the setup page and fill in **Fixed IP address** (an unused address on the same network,
outside the router's DHCP pool, e.g. 192.168.1.60) and **Router address** (e.g. 192.168.1.1). The subnet mask
defaults to 255.255.255.0. Leave all three empty to use DHCP. With a fixed IP the target is "connected" as soon as
it joins the WiFi. Firmware 0.1.8 also waits 3 minutes (not 45 s) for DHCP before restarting a connection attempt,
since a restart only delays a slow-but-working lease.
