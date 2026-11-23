param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    & $Python -B -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $Python -B -c @'
import ast
from pathlib import Path

files = list(Path(".").rglob("*.py")) + [Path("WirelessNetOptimizer.spec")]
for path in files:
    ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
print(f"AST OK: {len(files)} Python/spec files")
'@
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & node --check wireless_app/web/app.js
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & git diff --check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
