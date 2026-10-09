# GoldBot — installation Windows (dossier du bot = dossier parent de scripts\)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "`n=== GoldBot : installation ===`n" -ForegroundColor Yellow
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e ".[live]"
if (-not (Test-Path "config\config.toml")) { Copy-Item "config\config.example.toml" "config\config.toml" }
Write-Host "`nTerminé. Lis docs\GUIDE_DEMARRAGE.md puis, MT5 ouvert sur un compte DÉMO :" -ForegroundColor Green
Write-Host "  .venv\Scripts\goldbot fetch --source mt5 --tf H4"
Write-Host "  .venv\Scripts\goldbot run`n"
