#!/usr/bin/env bash
# Put the AutoSim bridge on a head unit over adb (userdebug / eng build, adb root) - Linux / macOS / Git Bash.
# Windows: scripts\android_bridge.cmd does the same.
#
#   ./android_bridge.sh deploy [--if can0] [--mode takeover|add] [--restart-vhal] [--permissive] [-s SERIAL]
#   ./android_bridge.sh check [--if can0]   environment report (docs/ANDROID_ENV_CHECKLIST.md), saved on the PC
#   ./android_bridge.sh start | stop | status | log | restore | forward
#
# deploy = push binary + script, create the vcan, (restart the VHAL), start the bridge, adb forward tcp:29536.
# Then in AutoSim: Channels -> port bridge://127.0.0.1:29536 -> Connect.
set -euo pipefail
cd "$(dirname "$0")"
ACTION=${1:-deploy}; shift || true
IFACE=can0; MODE=takeover; PORT=29536; BIND=127.0.0.1; RESTART=0; PERMISSIVE=0; BINARY=""; SERIAL=()
while [ $# -gt 0 ]; do
    case "$1" in
        --if) IFACE=$2; shift;;
        --mode) MODE=$2; shift;;
        --port) PORT=$2; shift;;
        --bind) BIND=$2; shift;;
        --binary) BINARY=$2; shift;;
        --restart-vhal) RESTART=1;;
        --permissive) PERMISSIVE=1;;
        -s) SERIAL=(-s "$2"); shift;;
        *) echo "unknown option $1"; exit 2;;
    esac
    shift
done
ADB=${ADB:-adb}
DIR=/data/local/tmp
adb_() { "$ADB" "${SERIAL[@]}" "$@"; }
sh_() { adb_ shell "$@"; }

start_bridge() {
    sh_ "kill $(pidof autosim-bridge) 2>/dev/null; setsid nohup $DIR/autosim-bridge -i $IFACE -b $BIND -p $PORT \
         > $DIR/autosim-bridge.log 2>&1 < /dev/null &"
    sleep 1
    sh_ "pidof autosim-bridge >/dev/null" || { echo "bridge did not start:"; sh_ "cat $DIR/autosim-bridge.log"; exit 1; }
    adb_ forward "tcp:$PORT" "tcp:$PORT"
    echo "bridge running on the head unit ($IFACE), forwarded to 127.0.0.1:$PORT"
    echo "AutoSim: Channels -> port bridge://127.0.0.1:$PORT -> Connect"
}

case "$ACTION" in
deploy)
    adb_ root >/dev/null; adb_ wait-for-device
    ABI=$(sh_ getprop ro.product.cpu.abi | tr -d '\r')
    BIN=${BINARY:-out/$ABI/autosim-bridge}
    [ -f "$BIN" ] || { echo "$BIN not found - run ./build_ndk.sh (ABI $ABI) or pass --binary"; exit 1; }
    adb_ push "$BIN" "$DIR/autosim-bridge" >/dev/null
    adb_ push autosim_vcan.sh "$DIR/autosim_vcan.sh" >/dev/null
    sh_ "chmod 755 $DIR/autosim-bridge $DIR/autosim_vcan.sh"
    if [ "$PERMISSIVE" = 1 ]; then sh_ setenforce 0; echo "SELinux permissive (until reboot)"; fi
    sh_ "sh $DIR/autosim_vcan.sh $MODE $IFACE"
    if [ "$RESTART" = 1 ]; then
        sh_ 'for p in $(ps -A -o PID,NAME | grep -i "automotive.vehicle" | grep -v grep | awk "{print \$1}"); do kill $p; done'
        echo "VHAL restarted (it opens $IFACE again)"
    fi
    start_bridge
    ;;
check)
    adb_ root >/dev/null 2>&1 || true; adb_ wait-for-device
    adb_ push android_env_check.sh "$DIR/android_env_check.sh" >/dev/null
    OUT="android_env_report_$(date +%Y%m%d_%H%M%S).txt"
    sh_ "sh $DIR/android_env_check.sh $IFACE" | tr -d '\r' | tee "$OUT"
    echo "report saved: $(pwd)/$OUT"
    ;;
start) start_bridge;;
stop) sh_ "kill \$(pidof autosim-bridge) 2>/dev/null" || true; adb_ forward --remove "tcp:$PORT" 2>/dev/null || true; echo stopped;;
status) sh_ "sh $DIR/autosim_vcan.sh status $IFACE; getenforce"; adb_ forward --list;;
log) sh_ "cat $DIR/autosim-bridge.log";;
restore) sh_ "kill \$(pidof autosim-bridge) 2>/dev/null; sh $DIR/autosim_vcan.sh restore $IFACE" || true;;
forward) adb_ forward "tcp:$PORT" "tcp:$PORT";;
*) echo "usage: $0 deploy|check|start|stop|status|log|restore|forward [options]"; exit 2;;
esac
