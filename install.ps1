# Instalador Windows: Claude Voice Swarm (camino principal de despliegue).
#   .\install.ps1              Python 3.11 (o 3.12) y torch solo CPU
#   .\install.ps1 -TorchCuda   torch desde PyPI en lugar del índice CPU de PyTorch
param([switch]$TorchCuda)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Write-Host "== Instalador Windows: Claude Voice Swarm ==" -ForegroundColor Cyan

function Invoke-Nativo {
    param([string]$Exe, [string[]]$Argumentos)
    & $Exe @Argumentos
    if ($LASTEXITCODE -ne 0) { throw "Falló: $Exe $($Argumentos -join ' ') (código $LASTEXITCODE)" }
}

# kokoro exige Python >=3.10,<3.13: se busca 3.11 (o 3.12) con el lanzador py, nunca 'python' a secas.
if (-Not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "No encuentro el lanzador 'py'. Instala Python 3.11 desde python.org (con el lanzador)."
}
$version = $null
$ErrorActionPreference = 'Continue'  # py escribe en stderr si falta esa versión; no es un error aquí
foreach ($v in @('3.11', '3.12')) {
    & py "-$v" -c "import sys" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $version = $v; break }
}
$ErrorActionPreference = 'Stop'
if (-Not $version) { throw "Hace falta Python 3.11 o 3.12 (py -0p lista los instalados). kokoro no funciona en 3.13+." }
Write-Host "Usando Python $version"

if (-Not (Test-Path ".\.venv\Scripts\python.exe")) {
    Invoke-Nativo py @("-$version", "-m", "venv", ".venv")
}
$py = ".\.venv\Scripts\python.exe"
$pyver = & $py -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($pyver -ne $version) { throw "El venv existente usa Python $pyver; bórralo (carpeta .venv) y repite." }
Invoke-Nativo $py @("-m", "pip", "install", "--upgrade", "pip")
if (-Not $TorchCuda) {
    # TORCH_CPU: torch solo CPU desde el índice oficial de PyTorch, antes de que kokoro arrastre otro.
    Invoke-Nativo $py @("-m", "pip", "install", "torch", "--index-url", "https://download.pytorch.org/whl/cpu")
}
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
    Write-Host "No hay empresas.json: es opcional. Copia empresas.json.example si quieres sesiones fijas," -ForegroundColor Green
    Write-Host "o pide al subjefe que las cree." -ForegroundColor Green
}
$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-Not $claude) {
    Write-Host "AVISO: no encuentro 'claude'. Instala Claude Code (instalador nativo) e inicia sesión." -ForegroundColor Yellow
} elseif ($claude.Source -match '\.(cmd|bat)$') {
    Write-Host "AVISO: 'claude' es un .cmd ($($claude.Source)); el puente necesita el claude.exe del instalador nativo." -ForegroundColor Yellow
}

New-Item -ItemType Directory -Force "datos" | Out-Null
Write-Host "Probar a mano:        .\run.ps1   (Ctrl+C para parar)" -ForegroundColor Green
Write-Host "Arranque automático:  .\registrar-tarea.ps1   (tarea programada al iniciar sesión)" -ForegroundColor Green
