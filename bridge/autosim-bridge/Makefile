# Linux host builds of autosim-bridge.
#   make         autosim-bridge       real SocketCAN (e.g. on a Linux PC with vcan0)
#   make fake    autosim-bridge-fake  "interface" = unix SOCK_SEQPACKET socket (AutoSim test-suite)
# Android: Android.bp (in the vendor tree) or build_ndk.sh (standalone NDK binary).
CC ?= cc
CFLAGS ?= -O2 -g
CFLAGS += -std=gnu11 -Wall -Wextra -Werror -Wshadow -Wformat=2

all: autosim-bridge

autosim-bridge: autosim_bridge.c abp.h
	$(CC) $(CFLAGS) -o $@ autosim_bridge.c

fake: autosim-bridge-fake

autosim-bridge-fake: autosim_bridge.c abp.h
	$(CC) $(CFLAGS) -DABRIDGE_FAKE_CAN -o $@ autosim_bridge.c

clean:
	rm -f autosim-bridge autosim-bridge-fake

.PHONY: all fake clean
