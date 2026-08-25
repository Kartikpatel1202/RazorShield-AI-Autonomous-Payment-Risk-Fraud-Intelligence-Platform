# One-time local setup: environment file, backend virtualenv, frontend packages.
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Host 'Created .env from .env.example - fill in the placeholder values.'
}

Write-Host '==> Backend virtualenv'
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install --upgrade pip
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt

Write-Host '==> Frontend packages'
Push-Location frontend
npm install
Pop-Location

Write-Host 'Setup complete.'
