#!/system/bin/sh
# Android environment check for CAN simulation (AutoSim VHAL bridge). Read-only except section 10, which creates
# and deletes a throw-away vcan when run as root.
#
#   VHAL_PID=<pid> overrides the VHAL lookup; first argument = interface (default can0).
#   adb root; adb push android_env_check.sh /data/local/tmp/; adb shell sh /data/local/tmp/android_env_check.sh
#   or: scripts\android_bridge.cmd check   /   ./android_bridge.sh check     (saves the report on the PC)
#
# Guide to reading the report: docs/ANDROID_ENV_CHECKLIST.md
# POSIX sh + toybox only (no awk / bash needed). Every command may fail: the report says "(n/a)" and goes on.

IF=${1:-can0}
ROOT=0; [ "$(id -u 2>/dev/null)" = "0" ] && ROOT=1

hdr() { echo; echo "==== $* ===="; }
kv() { printf '%-28s %s\n' "$1" "$2"; }
run() {                                   # run "<label>" cmd...
    label=$1; shift
    echo "--- $label: $*"
    out=$("$@" 2>&1); rc=$?
    if [ -n "$out" ]; then echo "$out"; fi
    [ $rc -ne 0 ] && [ -z "$out" ] && echo "(n/a, exit $rc)"
    return 0
}
have() { command -v "$1" >/dev/null 2>&1; }
prop() { getprop "$1" 2>/dev/null; }

echo "AutoSim Android environment check  ($(date 2>/dev/null))"
echo "interface checked: $IF   root: $([ $ROOT = 1 ] && echo yes || echo 'no - run adb root for full results')"

# ---------------------------------------------------------------------------------------------------------
hdr "1. Build"
kv "Android release / SDK" "$(prop ro.build.version.release) / $(prop ro.build.version.sdk)"
kv "build type / debuggable" "$(prop ro.build.type) / $(prop ro.debuggable)"
kv "fingerprint" "$(prop ro.build.fingerprint)"
kv "ABI" "$(prop ro.product.cpu.abi)"
kv "hardware / platform" "$(prop ro.hardware) / $(prop ro.board.platform)"
kv "SELinux" "$(getenforce 2>/dev/null || echo n/a)"
kv "automotive feature" "$(pm list features 2>/dev/null | grep -c android.hardware.type.automotive) (1 = AAOS)"
kv "kernel" "$(uname -r 2>/dev/null)"

# ---------------------------------------------------------------------------------------------------------
hdr "2. Kernel CAN support"
if [ -r /proc/config.gz ]; then
    echo "--- /proc/config.gz"
    zcat /proc/config.gz 2>/dev/null | grep -E '^(# )?CONFIG_(CAN|CAN_RAW|CAN_BCM|CAN_GW|CAN_VCAN|CAN_VXCAN|CAN_DEV|CAN_CALC_BITTIMING|CAN_M_CAN|CAN_FLEXCAN|CAN_MCP251XFD|CAN_MCP251X|VIRTIO_CAN|VSOCKETS|VIRTIO_VSOCKETS|SPI_SPIDEV|RPMSG_CHAR)[ =]'
else
    echo "/proc/config.gz not available (CONFIG_IKCONFIG_PROC off): rely on the module list and /proc/net below"
fi
run "CAN protocols registered" grep -i can /proc/net/protocols
run "/proc/net/can" ls /proc/net/can
run "loaded CAN-related modules" sh -c 'lsmod 2>/dev/null | grep -i -E "can|vcan|virtio|vsock|spidev"'
for d in /vendor/lib/modules /vendor_dlkm/lib/modules /system/lib/modules /lib/modules; do
    [ -d "$d" ] && run "modules on disk in $d" sh -c "ls $d | grep -i -E 'can|vsock|spidev'"
done

# ---------------------------------------------------------------------------------------------------------
hdr "3. CAN network interfaces"
if have ip; then
    run "CAN interfaces (type can)" ip -d link show type can
    run "virtual CAN (type vcan)" ip -d link show type vcan
    run "details + statistics of $IF" ip -d -s link show "$IF"
else
    echo "no 'ip' command"
fi
run "all net interfaces" ls /sys/class/net
for n in /sys/class/net/*; do
    t=$(cat "$n/type" 2>/dev/null)
    [ "$t" = "280" ] || continue               # ARPHRD_CAN
    i=${n##*/}
    echo "--- $i: mtu $(cat $n/mtu 2>/dev/null) (16 = classic CAN, 72 = CAN FD)  operstate $(cat $n/operstate 2>/dev/null)"
done

# ---------------------------------------------------------------------------------------------------------
hdr "4. Driver and bus behind $IF"
D=/sys/class/net/$IF/device
if [ -e "$D" ]; then
    kv "device path" "$(readlink -f $D 2>/dev/null)"
    kv "bus (subsystem)" "$(basename "$(readlink -f $D/subsystem 2>/dev/null)" 2>/dev/null)"
    kv "driver" "$(basename "$(readlink -f $D/driver 2>/dev/null)" 2>/dev/null)"
    kv "modalias" "$(cat $D/modalias 2>/dev/null)"
    kv "of_node compatible" "$(cat $D/of_node/compatible 2>/dev/null | tr '\0' ' ')"
elif [ -e /sys/class/net/$IF ]; then
    echo "$IF has no device link: a software interface (vcan / vxcan / slcan)"
else
    echo "$IF does not exist"
fi
[ $ROOT = 1 ] && run "kernel log (CAN / SPI / virtio)" sh -c 'dmesg 2>/dev/null | grep -i -E "can[0-9]|m_can|flexcan|mcp251|virtio_can|vcan|spi" | tail -n 40'

# ---------------------------------------------------------------------------------------------------------
hdr "5. Virtualisation / hypervisor hints"
run "virtio devices (device 0x0024 = can, 0x0013 = vsock, 0x0001 = net)" sh -c 'for d in /sys/bus/virtio/devices/*; do [ -e "$d" ] && echo "$(basename $d) device=$(cat $d/device) driver=$(basename $(readlink -f $d/driver) 2>/dev/null)"; done'
run "hypervisor node (device tree)" sh -c 'cat /proc/device-tree/hypervisor/compatible 2>/dev/null | tr "\0" " "; echo'
run "/sys/hypervisor/type" cat /sys/hypervisor/type
run "vsock device" ls -l /dev/vsock
[ $ROOT = 1 ] && run "kernel log (hypervisor)" sh -c 'dmesg 2>/dev/null | grep -i -E "hypervisor|gunyah|qnx|xen|kvm|virtio" | head -n 20'

# ---------------------------------------------------------------------------------------------------------
hdr "6. Vehicle HAL"
run "AIDL VHAL (service list)" sh -c 'service list 2>/dev/null | grep -i automotive.vehicle'
run "HIDL VHAL (lshal)" sh -c 'lshal 2>/dev/null | grep -i automotive.vehicle'
run "CAN bus HAL" sh -c '{ service list 2>/dev/null; lshal 2>/dev/null; } | grep -i automotive.can'
run "VHAL init scripts" sh -c 'grep -l -i "automotive.vehicle\|vehicle" /vendor/etc/init/*.rc /odm/etc/init/*.rc 2>/dev/null'
VP=${VHAL_PID:-}                              # override: VHAL_PID=1234 sh android_env_check.sh
[ -z "$VP" ] && VP=$(pidof android.hardware.automotive.vehicle-service 2>/dev/null)
[ -z "$VP" ] && VP=$(ps -A -o PID,NAME 2>/dev/null | grep -i "automotive.vehicle" | grep -v grep | head -n 1 | sed 's/^ *//' | cut -d' ' -f1)
if [ -n "$VP" ]; then
    kv "VHAL pid" "$VP"
    kv "VHAL process" "$(cat /proc/$VP/cmdline 2>/dev/null | tr '\0' ' ')"
    kv "VHAL binary" "$(readlink /proc/$VP/exe 2>/dev/null)"
    kv "VHAL SELinux domain" "$(cat /proc/$VP/attr/current 2>/dev/null | tr -d '\0')"
else
    echo "VHAL process not found by name - list: ps -A | grep -i vehicle"
fi

# ---------------------------------------------------------------------------------------------------------
hdr "7. What the VHAL talks to (open files and sockets)"
fam_of() {                                     # inode -> socket family (looked up in /proc/net tables)
    for f in tcp tcp6 udp udp6 unix netlink raw raw6 packet; do
        grep -q -w "$1" /proc/net/$f 2>/dev/null && { echo "$f"; return; }
    done
    echo "OTHER"
}
if [ -n "$VP" ] && [ -r /proc/$VP/fd ]; then
    devs=""; n_other=0
    for fd in /proc/$VP/fd/*; do
        t=$(readlink "$fd" 2>/dev/null)
        case "$t" in
            socket:*) ino=${t#socket:[}; ino=${ino%]}; f=$(fam_of "$ino")
                      echo "socket inode $ino -> $f"
                      [ "$f" = OTHER ] && n_other=$((n_other + 1)) ;;
            /dev/*)   echo "device $t"; devs="$devs $t" ;;
        esac
    done
    echo "sockets not in tcp/udp/unix/netlink/raw/packet: $n_other  (=> AF_CAN or AF_VSOCK, see section 8)"
    echo "device files:${devs:- none}"
else
    echo "cannot read /proc/<VHAL pid>/fd (needs root)"
fi

# ---------------------------------------------------------------------------------------------------------
hdr "8. Who listens on CAN interfaces (SocketCAN receive lists)"
run "/proc/net/can/rcvlist_all (device column = interface a CAN socket is bound to)" cat /proc/net/can/rcvlist_all
run "/proc/net/can/stats" cat /proc/net/can/stats

# ---------------------------------------------------------------------------------------------------------
hdr "9. SPI / UART / MCU links"
run "spidev nodes" sh -c 'ls -l /dev/spidev* 2>/dev/null'
run "SPI devices (driver per device)" sh -c 'for d in /sys/bus/spi/devices/*; do [ -e "$d" ] && echo "$(basename $d) modalias=$(cat $d/modalias 2>/dev/null) driver=$(basename $(readlink -f $d/driver) 2>/dev/null)"; done'
run "serial / rpmsg nodes" sh -c 'ls /dev/ttyHS* /dev/ttyS* /dev/ttyMSM* /dev/ttyLP* /dev/rpmsg* 2>/dev/null'
run "MCU / vehicle daemons" sh -c 'ps -A -o PID,USER,NAME 2>/dev/null | grep -i -E "mcu|ioc|vip|vehicle|can|car" | grep -v grep'

# ---------------------------------------------------------------------------------------------------------
hdr "10. AutoSim bridge prerequisites"
kv "ip / toybox" "$(command -v ip) / $(command -v toybox)"
if [ $ROOT = 1 ] && have ip; then
    if ip link add dev autosim_chk type vcan 2>/dev/null; then
        fd=$(ip link set autosim_chk mtu 72 2>/dev/null && echo "CAN FD ok" || echo "classic only")
        ip link del autosim_chk 2>/dev/null
        kv "create vcan" "yes ($fd)"
    else
        kv "create vcan" "NO - kernel lacks vcan (load vcan.ko or build it)"
    fi
else
    kv "create vcan" "not tested (needs root)"
fi
run "TCP port 29536 in use? (7360 = 29536 hex)" sh -c 'grep -i ":7360 " /proc/net/tcp /proc/net/tcp6 2>/dev/null || echo free'
run "IPv4 addresses (for Ethernet mode)" sh -c 'ip -4 -o addr show 2>/dev/null | grep -v " lo "'
run "autosim-bridge running" sh -c 'pidof autosim-bridge || echo no'

echo
echo "==== end of report ===="
