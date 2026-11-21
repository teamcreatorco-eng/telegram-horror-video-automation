$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    Write-Host 'First complete localhost onboarding: create .venv and install requirements. See ONBOARDING.md.'
    exit 1
}
Push-Location -LiteralPath $projectRoot
try {
    & $projectPython -m app.local_launcher
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
