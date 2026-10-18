# Arranca el puente en Windows y lo vuelve a lanzar si se cae.
#   .\run.ps1          en primer plano, con la salida en la consola
#   .\run.ps1 -Log     salida a datos\puente.log (lo usa la tarea programada)
param([switch]$Log)
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
$py = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-Not (Test-Path $py)) { throw "No existe venv. Ejecuta antes .\install.ps1" }
New-Item -ItemType Directory -Force "datos" | Out-Null
$logFile = Join-Path $PSScriptRoot "datos\puente.log"

$espera = 10
while ($true) {
    $inicio = Get-Date
    if ($Log) {
        & $py main.py *>> $logFile
    } else {
        & $py main.py
    }
    $codigo = $LASTEXITCODE
    # Si ha aguantado más de 5 minutos, el fallo no es de arranque: se vuelve a la espera corta.
    if (((Get-Date) - $inicio).TotalMinutes -gt 5) { $espera = 10 } else { $espera = [Math]::Min($espera * 2, 600) }
    $msg = "$(Get-Date -Format s) El puente terminó con código $codigo; reinicio en $espera s"
    if ($Log) { Add-Content -Path $logFile -Value $msg -Encoding UTF8 } else { Write-Host $msg -ForegroundColor Yellow }
    Start-Sleep -Seconds $espera
}
