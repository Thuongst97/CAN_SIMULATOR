#!/system/bin/sh
# Virtual CAN interface for the AutoSim bridge (runs on the head unit as root).
#
#   autosim_vcan.sh takeover [can0]   rename the real can0 to can0_hw, create a CAN FD vcan named can0
#                                     (the VHAL keeps opening "can0" and now talks to AutoSim)
#   autosim_vcan.sh add [vcan0]       only create a CAN FD vcan (VHAL configured to use it)
#   autosim_vcan.sh restore [can0]    delete the vcan, give the real interface its name back
#   autosim_vcan.sh status [can0]
#
# The VHAL binds its socket to an interface index when it starts: after takeover / restore on a running system,
# restart the VHAL so it opens the new interface (the script prints how).
set -u
MODE=${1:-status}
IF=${2:-can0}
HW=${IF}_hw

say() {                                     # stdout + logcat (not named `log`: that is Android's logcat tool)
    echo "autosim_vcan: $*"
    [ -x /system/bin/log ] && /system/bin/log -t autosim_vcan "$*"
    return 0
}
is_vcan() { ip -d link show "$1" 2>/dev/null | grep -q " vcan "; }
exists() { ip link show "$1" >/dev/null 2>&1; }

load_vcan() {
    ip link add dev autosim_probe type vcan 2>/dev/null && { ip link del autosim_probe; return 0; }
    modprobe vcan 2>/dev/null && return 0
    for ko in /vendor/lib/modules/vcan.ko /vendor_dlkm/lib/modules/vcan.ko /system/lib/modules/vcan.ko \
              /data/local/tmp/vcan.ko; do
        [ -f "$ko" ] && insmod "$ko" 2>/dev/null && return 0
    done
    say "ERROR: the kernel has no vcan support (CONFIG_CAN_VCAN); build vcan.ko for this kernel"
    return 1
}

make_vcan() {
    ip link add dev "$1" type vcan || { say "ERROR: cannot create vcan $1"; return 1; }
    ip link set "$1" mtu 72 || say "warning: $1 stays classic CAN (mtu 72 refused)"
    ip link set "$1" up
}

vhal_hint() {
    pid=$(pidof android.hardware.automotive.vehicle@V3-default-service 2>/dev/null)
    [ -z "$pid" ] && pid=$(ps -A -o PID,NAME | grep -i "automotive.vehicle" | grep -v grep | head -n1 | awk '{print $1}')
    if [ -n "$pid" ]; then
        say "VHAL is running (pid $pid): restart it so it opens the new $IF -> kill $pid (init restarts it)"
    fi
}

case "$MODE" in
takeover)
    load_vcan || exit 1
    if exists "$IF" && is_vcan "$IF"; then
        say "$IF is already a vcan - nothing to do"
        exit 0
    fi
    if exists "$IF"; then
        if exists "$HW"; then say "ERROR: $HW already exists"; exit 1; fi
        ip link set "$IF" down
        ip link set "$IF" name "$HW" || { say "ERROR: cannot rename $IF"; exit 1; }
        say "real $IF renamed $HW (kept down)"
    fi
    make_vcan "$IF" || exit 1
    say "vcan $IF ready (CAN FD, up)"
    vhal_hint
    ;;
add)
    load_vcan || exit 1
    exists "$IF" && { say "$IF already exists"; exit 0; }
    make_vcan "$IF" || exit 1
    say "vcan $IF ready (CAN FD, up)"
    ;;
restore)
    if exists "$IF" && is_vcan "$IF"; then ip link del "$IF"; say "vcan $IF deleted"; fi
    if exists "$HW"; then
        ip link set "$HW" name "$IF" && ip link set "$IF" up
        say "real interface is $IF again"
    fi
    vhal_hint
    ;;
status)
    for i in "$IF" "$HW"; do exists "$i" && ip -d link show "$i"; done
    pidof autosim-bridge >/dev/null && echo "autosim-bridge: running (pid $(pidof autosim-bridge))" \
        || echo "autosim-bridge: not running"
    ;;
*)
    echo "usage: $0 takeover|add|restore|status [interface]"
    exit 2
    ;;
esac
