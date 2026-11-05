#!/usr/bin/env bash
# Standalone Android binaries with the NDK (no AOSP tree needed): static, so they run from /data/local/tmp.
#   ANDROID_NDK_HOME=/path/to/ndk ./build_ndk.sh            -> out/arm64-v8a/autosim-bridge, out/x86_64/...
set -euo pipefail
cd "$(dirname "$0")"
NDK=${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}
[ -n "$NDK" ] || { echo "set ANDROID_NDK_HOME"; exit 2; }
HOST=$(ls "$NDK/toolchains/llvm/prebuilt" | head -n1)
BIN="$NDK/toolchains/llvm/prebuilt/$HOST/bin"
API=${API:-29}
for abi in arm64-v8a x86_64; do
    case $abi in arm64-v8a) triple=aarch64-linux-android;; x86_64) triple=x86_64-linux-android;; esac
    mkdir -p "out/$abi"
    "$BIN/${triple}${API}-clang" -std=gnu11 -O2 -Wall -Wextra -Werror -static -s \
        -o "out/$abi/autosim-bridge" autosim_bridge.c
    echo "built out/$abi/autosim-bridge"
done
