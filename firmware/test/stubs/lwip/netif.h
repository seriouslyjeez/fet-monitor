#pragma once
struct dhcp { unsigned char state = 0; unsigned char tries = 0; };
struct netif { dhcp d; };
static netif g_stub_netif;
static netif* netif_default = &g_stub_netif;
#define netif_dhcp_data(n) (&(n)->d)
