<#
.SYNOPSIS
    Put the AutoSim bridge on an Android head unit over adb (userdebug / eng build) - scripts\android_bridge.cmd.

.DESCRIPTION
    deploy   push autosim-bridge + autosim_vcan.sh to /data/local/tmp, create the virtual CAN interface,
             optionally restart the VHAL, start the bridge and `adb forward tcp:29536 tcp:29536` (default action)
    build    build the Android binaries with the NDK (ANDROID_NDK_HOME) into bridge\autosim-bridge\out\<abi>
    start    (re)start the bridge only           stop     stop it and remove the forward
    status   interfaces, bridge process, SELinux  log      the bridge's log
    restore  stop the bridge, delete the vcan, give the real interface its name back
    forward  only `adb forward`

    Then in AutoSim: Channels -> port bridge://127.0.0.1:29536 -> Connect. Details: docs\ANDROID_BRIDGE.md

.EXAMPLE
    scripts\android_bridge.cmd deploy -RestartVhal
    scripts\android_bridge.cmd deploy -Mode add -Interface vcan0
    scripts\android_bridge.cmd restore
#>
param(
    [ValidateSet("deploy", "build", "start", "stop", "status", "log", "restore", "forward")]
    [string]$Action = "deploy",
    [string]$Interface = "can0",              # interface the VHAL uses
    [ValidateSet("takeover", "add")]
    [string]$Mode = "takeover",               # takeover: vcan replaces can0 (renamed can0_hw); add: new vcan only
    [int]$Port = 29536,
    [string]$Bind = "127.0.0.1",              # 0.0.0.0 to reach the bridge over Ethernet instead of adb
    [string]$Serial = "",
    [string]$Binary = "",
    [switch]$RestartVhal,                     # kill the VHAL so init restarts it on the new interface
    [switch]$Permissive                       # setenforce 0 until reboot (if SELinux blocks the bridge)
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_tools.ps1")
$Src = Join-Path (Split-Path $PSScriptRoot -Parent) "bridge\autosim-bridge"
$Dir = "/data/local/tmp"

function Find-Adb {
    $cmd = Get-Command adb -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return First-Existing @("$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe",
                            "$env:ANDROID_HOME\platform-tools\adb.exe", "$env:ANDROID_SDK_ROOT\platform-tools\adb.exe")
}

function Adb {
    $ErrorActionPreference = "Continue"       # PowerShell 5.1 turns adb's stderr lines into terminating errors
    $a = @()
    if ($Serial) { $a += @("-s", $Serial) }
    $out = & $script:AdbExe @a @args 2>&1
    $code = $LASTEXITCODE
    return [pscustomobject]@{ Code = $code; Text = (($out | ForEach-Object { "$_" }) -join "`n").Trim() }
}

function Shell([string]$cmd) { return Adb shell $cmd }

function Show($r) { if ($r.Text) { Write-Host $r.Text } }

function Start-Bridge {
    Show (Shell "kill `$(pidof autosim-bridge) 2>/dev/null; setsid nohup $Dir/autosim-bridge -i $Interface -b $Bind -p $Port > $Dir/autosim-bridge.log 2>&1 < /dev/null &")
    Start-Sleep -Seconds 1
    if ((Shell "pidof autosim-bridge").Code -ne 0) {
        Write-Host "The bridge did not start:" -ForegroundColor Red
        Show (Shell "cat $Dir/autosim-bridge.log")
        exit 1
    }
    $f = Adb forward "tcp:$Port" "tcp:$Port"
    if ($f.Code -ne 0) { Write-Host "adb forward failed: $($f.Text)" -ForegroundColor Red; exit 1 }
    Write-Host "Bridge running on the head unit ($Interface), forwarded to 127.0.0.1:$Port" -ForegroundColor Green
    Write-Host "AutoSim: Channels -> port bridge://127.0.0.1:$Port -> Connect"
}

if ($Action -eq "build") {
    $ndk = $env:ANDROID_NDK_HOME
    if (-not $ndk) { $ndk = $env:ANDROID_NDK_ROOT }
    if (-not $ndk) {
        $ndk = First-Existing @("$env:LOCALAPPDATA\Android\Sdk\ndk\*")
    }
    if (-not $ndk) { Write-Host "NDK not found: set ANDROID_NDK_HOME" -ForegroundColor Red; exit 2 }
    $bin = Get-ChildItem (Join-Path $ndk "toolchains\llvm\prebuilt") | Select-Object -First 1
    $bin = Join-Path $bin.FullName "bin"
    foreach ($abi in @(@("arm64-v8a", "aarch64-linux-android"), @("x86_64", "x86_64-linux-android"))) {
        $out = Join-Path $Src "out\$($abi[0])"
        New-Item -ItemType Directory -Force $out | Out-Null
        $cc = Join-Path $bin "$($abi[1])29-clang.cmd"
        & $cc -std=gnu11 -O2 -Wall -Wextra -Werror -static -s -o (Join-Path $out "autosim-bridge") `
            (Join-Path $Src "autosim_bridge.c")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Host "built $out\autosim-bridge"
    }
    exit 0
}

$script:AdbExe = Find-Adb
if (-not $script:AdbExe) { Write-Host "adb not found: install Android platform-tools" -ForegroundColor Red; exit 2 }

switch ($Action) {
    "deploy" {
        Adb root | Out-Null
        Adb wait-for-device | Out-Null
        $abi = (Shell "getprop ro.product.cpu.abi").Text
        $bin = if ($Binary) { $Binary } else { Join-Path $Src "out\$abi\autosim-bridge" }
        if (-not (Test-Path $bin)) {
            Write-Host "$bin not found - run 'scripts\android_bridge.cmd build' (device ABI: $abi) or pass -Binary" `
                -ForegroundColor Red
            exit 1
        }
        foreach ($p in @(@($bin, "$Dir/autosim-bridge"), @((Join-Path $Src "autosim_vcan.sh"), "$Dir/autosim_vcan.sh"))) {
            $r = Adb push $p[0] $p[1]
            if ($r.Code -ne 0) { Write-Host "adb push failed: $($r.Text)" -ForegroundColor Red; exit 1 }
        }
        Show (Shell "chmod 755 $Dir/autosim-bridge $Dir/autosim_vcan.sh")
        if ($Permissive) { Show (Shell "setenforce 0"); Write-Host "SELinux permissive until reboot" }
        $r = Shell "sh $Dir/autosim_vcan.sh $Mode $Interface"
        Show $r
        if ($r.Code -ne 0) { exit 1 }
        if ($RestartVhal) {
            Show (Shell 'for p in $(ps -A -o PID,NAME | grep -i automotive.vehicle | grep -v grep | awk ''{print $1}''); do kill $p; done')
            Write-Host "VHAL restarted: it opens $Interface again"
        }
        Start-Bridge
    }
    "start" { Start-Bridge }
    "stop" {
        Show (Shell "kill `$(pidof autosim-bridge) 2>/dev/null; true")
        Adb forward --remove "tcp:$Port" | Out-Null
        Write-Host "stopped"
    }
    "status" {
        Show (Shell "sh $Dir/autosim_vcan.sh status $Interface; echo SELinux: `$(getenforce)")
        Show (Adb forward --list)
    }
    "log" { Show (Shell "cat $Dir/autosim-bridge.log") }
    "restore" { Show (Shell "kill `$(pidof autosim-bridge) 2>/dev/null; sh $Dir/autosim_vcan.sh restore $Interface") }
    "forward" { Show (Adb forward "tcp:$Port" "tcp:$Port") }
}
