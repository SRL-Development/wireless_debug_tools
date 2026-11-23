param(
    [string]$AppExe = "dist/WirelessNetOptimizer-Enhanced.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$sourceExe = [IO.Path]::GetFullPath((Join-Path $root $AppExe))
$script = Join-Path $root "installer/WirelessNetOptimizer.iss"

if (-not (Test-Path -LiteralPath $sourceExe)) {
    throw "Application EXE not found: $sourceExe"
}

$compilerCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs/Inno Setup 6/ISCC.exe"),
    "C:/Program Files (x86)/Inno Setup 6/ISCC.exe",
    "C:/Program Files/Inno Setup 6/ISCC.exe"
)
$compiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $compiler) {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { $compiler = $cmd.Source }
}
if (-not $compiler) {
    throw "Inno Setup 6 compiler was not found. Install JRSoftware.InnoSetup with winget first."
}

Push-Location $root
try {
    & $compiler "/DSourceExe=$sourceExe" $script
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $setup = Join-Path $root "dist/WirelessNetOptimizer-Setup.exe"
    if (-not (Test-Path -LiteralPath $setup)) {
        throw "Installer build completed without expected artifact: $setup"
    }
    Get-Item -LiteralPath $setup | Select-Object FullName, Length, LastWriteTime
} finally {
    Pop-Location
}
