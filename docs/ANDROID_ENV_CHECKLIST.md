# Android environment checklist (CAN simulation)

Use this document to survey a head unit or an Android VM before simulating CAN. The goal is to answer four
questions:
- How does the VHAL reach CAN: `AF_CAN`, SPI / UART to an MCU, or vsock / IPC to a host?
- Which driver creates `can0`, and on which bus?
- Which kind of VHAL is it (AIDL or HIDL), and which process runs it?
- Can the AutoSim bridge run: is there vcan, root, and does SELinux allow it?

Layers and simulation points: [ANDROID_CAN_LAYERS.md](ANDROID_CAN_LAYERS.md). Deploying the bridge:
[ANDROID_BRIDGE.md](ANDROID_BRIDGE.md).

---

## 0. Automatic run

```
scripts\android_bridge.cmd check                       (Windows)
bridge/autosim-bridge/android_bridge.sh check          (Linux / macOS)
```

This copies `bridge/autosim-bridge/android_env_check.sh` to the device, runs it and saves the report as
`android_env_report_<time>.txt` on the PC. The report has 10 sections, numbered like the sections below.

By hand:
```
adb root
adb push bridge/autosim-bridge/android_env_check.sh /data/local/tmp/
adb shell sh /data/local/tmp/android_env_check.sh can0
```

- The script only reads. Section 10 alone creates and immediately deletes a temporary vcan (`autosim_chk`) when run
  as root.
- Without root (a `user` build) it still runs, but sections 4, 7 and 8 are incomplete.
- The interface to check is the first argument (default `can0`).
- If the script cannot find the VHAL process by name, give the pid: `VHAL_PID=1234 sh …`.

The sections below explain each check: the command, what to look at, and what the result means.

---

## 1. Build

| Check | Command | Expect |
|---|---|---|
| Android version | `getprop ro.build.version.release` / `ro.build.version.sdk` | SDK ≥ 33 (Android 13) usually has an AIDL VHAL; older versions usually HIDL |
| Build type | `getprop ro.build.type`, `ro.debuggable` | `userdebug` or `eng`, `debuggable=1`; otherwise no `adb root` and no bench deployment of the bridge |
| ABI | `getprop ro.product.cpu.abi` | `arm64-v8a` or `x86_64`, to pick the NDK binary |
| SELinux | `getenforce` | `Enforcing` is normal; if the bridge is blocked, see `adb logcat \| grep avc` |
| Is it AAOS | `pm list features \| grep automotive` | `android.hardware.type.automotive` |

## 2. Kernel CAN support

| Check | Command | Expect / meaning |
|---|---|---|
| Kernel config | `zcat /proc/config.gz \| grep CONFIG_CAN` | `CONFIG_CAN`, `CONFIG_CAN_RAW`; `CONFIG_CAN_VCAN=y` or `=m` for vcan |
| Registered protocols | `grep -i can /proc/net/protocols` | `CAN_RAW` means `AF_CAN` raw sockets work |
| SocketCAN running | `ls /proc/net/can` | the directory exists when the `can` module is loaded |
| Modules | `lsmod \| grep -i can`, `ls /vendor/lib/modules /vendor_dlkm/lib/modules \| grep can` | `vcan.ko`, `can-raw.ko`, controller drivers (`m_can`, `flexcan`, `mcp251xfd`…) |

Without `/proc/config.gz` (kernel built without `CONFIG_IKCONFIG_PROC`), rely on `/proc/net/protocols` and the module
list.

## 3. CAN interfaces

| Check | Command | Expect / meaning |
|---|---|---|
| Real CAN interfaces | `ip -d link show type can` | e.g. `can0`, with a line `can <FD> state ERROR-ACTIVE … bitrate 500000 … dbitrate 2000000` |
| Existing vcans | `ip -d link show type vcan` | usually empty; after `takeover`, `can0` shows here |
| Details + statistics | `ip -d -s link show can0` | MTU **72** is CAN FD, **16** is classic CAN; RX / TX / error counters show traffic |
| No `ip` | `cat /sys/class/net/*/type`, `cat /sys/class/net/can0/mtu` | type **280** is CAN |

## 4. Which driver, on which bus

| Check | Command | Meaning |
|---|---|---|
| Driver | `basename $(readlink -f /sys/class/net/can0/device/driver)` | `m_can_platform`, `flexcan`, `rcar_canfd` … are controllers in the SoC (MMIO); `mcp251xfd` is a CAN chip on **SPI**; `virtio_can` is CAN provided by the **hypervisor** |
| Bus | `basename $(readlink -f /sys/class/net/can0/device/subsystem)` | `platform`, `spi`, `virtio`, `pci` or `usb` |
| Device path | `readlink -f /sys/class/net/can0/device` | e.g. `…/spi0/spi0.0` or `…/virtio5` |
| Device tree | `cat /sys/class/net/can0/device/of_node/compatible` | e.g. `bosch,m_can`, `microchip,mcp2518fd` |
| No `device` link | — | a software interface (vcan, vxcan, slcan) |
| Kernel log | `dmesg \| grep -i -E "can|m_can|flexcan|mcp251|virtio_can"` | probe errors, bit rates, bus-off |

## 5. Virtualisation / hypervisor

| Check | Command | Meaning |
|---|---|---|
| virtio devices | `cat /sys/bus/virtio/devices/*/device` | `0x0024` is virtio-can; `0x0013` is vsock (a common channel between the VHAL and the host); `0x0001` is network |
| Hypervisor | `cat /proc/device-tree/hypervisor/compatible`, `cat /sys/hypervisor/type`, `dmesg \| grep -i hypervisor` | Gunyah, Xen, KVM, QNX… |
| vsock | `ls -l /dev/vsock` | vsock means the VHAL may talk to the host VM over gRPC or IPC (see section 7) |

## 6. Vehicle HAL

| Check | Command | Meaning |
|---|---|---|
| AIDL VHAL | `service list \| grep automotive.vehicle` | `android.hardware.automotive.vehicle.IVehicle/default` → stable AIDL (Android 13+), Binder |
| HIDL VHAL | `lshal \| grep automotive.vehicle` | `android.hardware.automotive.vehicle@2.0::IVehicle/default` → HIDL, hwbinder |
| CAN bus HAL | `service list \| grep automotive.can`, `lshal \| grep automotive.can` | if present, the VHAL may go through the CAN HAL (`ICanBus`) instead of opening a socket itself |
| VHAL process | `ps -A \| grep -i vehicle` | process name, e.g. `android.hardware.automotive.vehicle@V3-<vendor>-service` |
| Binary + domain | `readlink /proc/<pid>/exe`, `cat /proc/<pid>/attr/current` | needed to read the vendor's sepolicy |
| Init file | `grep -l vehicle /vendor/etc/init/*.rc` | arguments and interface name in the start command |

## 7. What the VHAL talks to

The most important section. List the VHAL's file descriptors:

```
adb shell ls -l /proc/<VHAL pid>/fd
```

- **sockets**: every `socket:[inode]` line is a socket. Look the inode up in
  `/proc/net/{tcp,tcp6,udp,unix,netlink,raw,packet}`. A socket found in **none** of those tables is `AF_CAN` or
  `AF_VSOCK`. The script does this lookup automatically.
- **devices**: `/dev/spidev*` or `/dev/tty*` means the VHAL talks to an MCU directly; `/dev/vsock` or
  `/dev/rpmsg*` means it talks to a host or a coprocessor.
- Only `unix` sockets and binder: the VHAL relies on another daemon for CAN. Find that daemon in section 9.

## 8. Who listens on the CAN interface

```
adb shell cat /proc/net/can/rcvlist_all
```

This table lists the receive filters of every CAN socket, with a **device** column naming the interface the socket
is bound to.
- A line with device `can0` proves that a process has an `AF_CAN` socket open on `can0`.
- Combined with section 7: if the VHAL has an "OTHER" socket and `rcvlist_all` shows `can0`, the VHAL uses `AF_CAN`.
- `cat /proc/net/can/stats` shows how many frames were received and sent.

## 9. SPI / UART / MCU

| Check | Command | Meaning |
|---|---|---|
| spidev | `ls -l /dev/spidev*` | user space talks SPI directly (an MCU protocol) |
| SPI devices and drivers | `ls /sys/bus/spi/devices`, then `modalias`, `driver` | `mcp251xfd` is a CAN chip (see section 4); `spidev` is a raw channel to an MCU |
| UART / rpmsg | `ls /dev/ttyHS* /dev/ttyS* /dev/ttyMSM* /dev/rpmsg*` | an MCU or coprocessor over serial or rpmsg |
| MCU daemon | `ps -A \| grep -i -E "mcu\|ioc\|vip\|vehicle\|can"` | a vendor daemon between the VHAL and the MCU |

## 10. Prerequisites for the AutoSim bridge

| Check | Command | Expect |
|---|---|---|
| A vcan can be created | `ip link add dev t type vcan && ip link set t mtu 72 && ip link del t` (root) | success; otherwise load or build `vcan.ko` |
| TCP port | `grep -i ":7360 " /proc/net/tcp` (7360 is 29536 in hex) | no line, i.e. the port is free |
| IP for Ethernet mode | `ip -4 addr` | the head unit's IP, used when the bridge runs with `-b 0.0.0.0` |
| Bridge running | `pidof autosim-bridge` | — |

---

## Reading the results: choosing how to simulate

| Survey result | Case | How to simulate |
|---|---|---|
| The VHAL has an "OTHER" socket, `rcvlist_all` shows `can0`; driver `m_can` / `flexcan` / `mcp251xfd` / `virtio_can` | VHAL → `AF_CAN` → CAN driver (MMIO, SPI or virtio) | **B · vcan + bridge**: `android_bridge check`, then `deploy -RestartVhal` |
| As above but **no vcan can be created** | `CONFIG_CAN_VCAN` missing | build a `vcan.ko` for that kernel (GKI vendor module); meanwhile use **E · HIL** |
| The VHAL opens `/dev/spidev*` or `/dev/tty*`, or there is an MCU daemon; no `can0` | VHAL → proprietary protocol → MCU owns CAN | **E · HIL** on the bus; or replace the daemon's transport / build a "fake MCU" (needs the protocol spec) |
| The VHAL only has vsock, `/dev/vsock` exists, no `can0` | VHAL → gRPC / IPC → host VM owns CAN | **A** (GRPC VHAL) or **C** (fake IPC server); or **D** if the host can be changed |
| The VHAL only has binder / unix sockets, and there is a CAN bus HAL | VHAL → CAN HAL (`ICanBus`) → `AF_CAN` | still **B**: configure the CAN HAL's bus as the vcan |
| `user` build, no root | — | ask for a `userdebug` build; or **E · HIL** |

## Result template

| Item | Value |
|---|---|
| Device / build fingerprint | |
| Android / SDK / build type | |
| SELinux | |
| Hypervisor (if any) | |
| CAN interface, MTU, bitrate / dbitrate | |
| Driver / bus | |
| VHAL: AIDL / HIDL, process name | |
| VHAL talks through | AF_CAN / spidev / tty / vsock / CAN HAL |
| vcan (FD) can be created | |
| Chosen simulation | B / A / C / D / E |
| Notes | |
