Write-Host "== Instalador Windows: Claude Voice Swarm ==" -ForegroundColor Cyan
Set-Location $PSScriptRoot

if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install --id=Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    winget install --id=eSpeak-NG.eSpeak-NG -e --accept-source-agreements --accept-package-agreements
} else {
    Write-Host "Sin winget: instala ffmpeg y espeak-ng y añádelos al PATH." -ForegroundColor Yellow
}

python -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt

if (-Not (Test-Path ".env")) {
    $topic = "cvs-" + (.\venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(12))")
    (Get-Content ".env.example") -replace '^NTFY_TOPIC=.*', "NTFY_TOPIC=$topic" | Set-Content ".env"
    Write-Host ".env creado. Edítalo con tus claves. Tema ntfy: $topic" -ForegroundColor Green
}

if (-Not (Test-Path "empresas.json")) {
    Copy-Item "empresas.json.example" "empresas.json"
    Write-Host "empresas.json creado: edítalo con tus empresas y sus carpetas." -ForegroundColor Green
}
if (-Not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host "AVISO: no encuentro 'claude'. Instala Claude Code e inicia sesión." -ForegroundColor Yellow
}

Write-Host "Arrancar: .\venv\Scripts\python.exe main.py" -ForegroundColor Green
