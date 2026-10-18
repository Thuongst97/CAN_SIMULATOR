# Shared helpers for the scripts: locate the tools whether they are on PATH, in their default install folder or in
# the project-local .tools\ folder. Dot-source it:  . (Join-Path $PSScriptRoot "_tools.ps1")

$Script:ProjectRoot = Split-Path $PSScriptRoot -Parent

function Update-SessionPath {
    # pick up PATH changes made by installers without opening a new terminal
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function First-Existing([string[]]$Candidates) {
    foreach ($c in $Candidates) {
        $hit = Get-Item $c -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return First-Existing @("$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.cargo\bin\uv.exe",
                            "$env:LOCALAPPDATA\Microsoft\WinGet\Links\uv.exe")
}

function Find-Iscc {
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return First-Existing @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")
}

function Find-DfuUtil {
    $cmd = Get-Command dfu-util -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return First-Existing @("$Script:ProjectRoot\.tools\dfu-util\*\win64\dfu-util.exe",
                            "$Script:ProjectRoot\.tools\dfu-util\win64\dfu-util.exe",
                            "$Script:ProjectRoot\.tools\dfu-util\*\dfu-util.exe")
}

function Find-ArmGcc {
    $cmd = Get-Command arm-none-eabi-gcc -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return First-Existing @("${env:ProgramFiles(x86)}\Arm GNU Toolchain arm-none-eabi\*\bin\arm-none-eabi-gcc.exe",
                            "$env:ProgramFiles\Arm GNU Toolchain arm-none-eabi\*\bin\arm-none-eabi-gcc.exe",
                            "${env:ProgramFiles(x86)}\GNU Arm Embedded Toolchain\*\bin\arm-none-eabi-gcc.exe",
                            "$Script:ProjectRoot\.tools\arm-gnu-toolchain\*\bin\arm-none-eabi-gcc.exe")
}

function Find-Make {
    $cmd = Get-Command make -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return First-Existing @("$env:LOCALAPPDATA\Microsoft\WinGet\Links\make.exe",
                            "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\ezwinports.make*\bin\make.exe",
                            "C:\msys64\usr\bin\make.exe")
}

function Find-GitUsrBin {
    $git = Get-Command git -ErrorAction SilentlyContinue
    $cands = @()
    if ($git) { $cands += (Join-Path (Split-Path (Split-Path $git.Source -Parent) -Parent) "usr\bin") }
    $cands += "$env:ProgramFiles\Git\usr\bin"
    foreach ($c in $cands) { if (Test-Path (Join-Path $c "sh.exe")) { return $c } }
    return $null
}
