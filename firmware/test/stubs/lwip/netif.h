#pragma once
struct dhcp { unsigned char state = 0; unsigned char tries = 0; };
struct netif { dhcp d; char name[2] = {'s','t'}; unsigned char num = 0; netif* next = nullptr; };
static netif g_stub_netif;
static netif* netif_default = &g_stub_netif;
#define netif_dhcp_data(n) (&(n)->d)
static netif* netif_list = &g_stub_netif;
inline bool netif_is_up(netif*) { return true; }
