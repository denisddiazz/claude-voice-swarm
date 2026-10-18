# Instalador Windows: Claude Voice Swarm.
# El despliegue recomendado es WSL2 (ver README, "Despliegue en WSL2"); esto es para pruebas en Windows.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Write-Host "== Instalador Windows: Claude Voice Swarm ==" -ForegroundColor Cyan

function Invoke-Nativo {
    param([string]$Exe, [string[]]$Argumentos)
    & $Exe @Argumentos
    if ($LASTEXITCODE -ne 0) { throw "Falló: $Exe $($Argumentos -join ' ') (código $LASTEXITCODE)" }
}

# kokoro exige Python >=3.10,<3.13: se busca 3.12 o 3.11 con el lanzador py, nunca 'python' a secas.
if (-Not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "No encuentro el lanzador 'py'. Instala Python 3.12 o 3.11 desde python.org (con el lanzador)."
}
$version = $null
$ErrorActionPreference = 'Continue'  # py escribe en stderr si falta esa versión; no es un error aquí
foreach ($v in @('3.12', '3.11')) {
    & py "-$v" -c "import sys" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $version = $v; break }
}
$ErrorActionPreference = 'Stop'
if (-Not $version) { throw "Hace falta Python 3.12 o 3.11 (py -0p lista los instalados). kokoro no funciona en 3.13+." }
Write-Host "Usando Python $version"

if (-Not (Test-Path ".\venv\Scripts\python.exe")) {
    Invoke-Nativo py @("-$version", "-m", "venv", "venv")
}
$py = ".\venv\Scripts\python.exe"
$pyver = & $py -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($pyver -ne $version) { throw "El venv existente usa Python $pyver; bórralo (carpeta venv) y repite." }
Invoke-Nativo $py @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Nativo $py @("-m", "pip", "install", "-r", "requirements.txt")

if (-Not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "AVISO: falta ffmpeg (solo para enviar notas de voz). Instálalo con: winget install --id=Gyan.FFmpeg -e" -ForegroundColor Yellow
}

if (-Not (Test-Path ".env")) {
    $topic = "cvs-" + (& $py -c "import secrets; print(secrets.token_hex(12))")
    $lineas = (Get-Content ".env.example" -Encoding UTF8) -replace '^NTFY_TOPIC=.*', "NTFY_TOPIC=$topic"
    [System.IO.File]::WriteAllLines((Join-Path $PSScriptRoot ".env"), $lineas, (New-Object System.Text.UTF8Encoding $false))
    Write-Host ".env creado. Edítalo con tu TELEGRAM_TOKEN. Tema ntfy: $topic" -ForegroundColor Green
}

if (-Not (Test-Path "empresas.json")) {
    Copy-Item "empresas.json.example" "empresas.json"
    Write-Host "empresas.json creado: edítalo con tus empresas y sus carpetas." -ForegroundColor Green
}
if (-Not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host "AVISO: no encuentro 'claude'. Instala Claude Code e inicia sesión." -ForegroundColor Yellow
}

Write-Host "Arrancar: `$env:PYTHONUTF8=1; .\venv\Scripts\python.exe main.py" -ForegroundColor Green
