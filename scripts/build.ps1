param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    & $Python -B -m PyInstaller --noconfirm --clean WirelessNetOptimizer.spec
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $exe = Join-Path $root "dist/WirelessNetOptimizer.exe"
    if (-not (Test-Path -LiteralPath $exe)) {
        throw "Build completed without expected artifact: $exe"
    }
    Get-Item -LiteralPath $exe | Select-Object FullName, Length, LastWriteTime
} finally {
    Pop-Location
}
