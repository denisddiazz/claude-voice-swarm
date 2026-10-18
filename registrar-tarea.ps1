# Registra una tarea programada que arranca el puente al iniciar sesión, con tu usuario
# (la sesión de Claude Code es tuya: con la cuenta SYSTEM no habría login).
#   .\registrar-tarea.ps1            registra (o sustituye) la tarea
#   .\registrar-tarea.ps1 -Quitar    la elimina
#   .\registrar-tarea.ps1 -Parar     para la tarea y termina todo el árbol del puente
#                                    (run.ps1 y python main.py de este repo)
# Gestión: Start-ScheduledTask / Stop-ScheduledTask -TaskName ClaudeVoiceSwarm
param([switch]$Quitar, [switch]$Parar, [string]$Nombre = "ClaudeVoiceSwarm")
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if ($Quitar) {
    Unregister-ScheduledTask -TaskName $Nombre -Confirm:$false
    Write-Host "Tarea $Nombre eliminada."
    return
}
if ($Parar) {
    try {
        Stop-ScheduledTask -TaskName $Nombre -ErrorAction Stop
        Write-Host "Tarea $Nombre parada."
    } catch {
        Write-Host "Tarea $Nombre no está en marcha o no existe: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    $pyMin = (Join-Path $PSScriptRoot ".venv\Scripts\python.exe").ToLowerInvariant()
    $ps1Min = (Join-Path $PSScriptRoot "run.ps1").ToLowerInvariant()
    $objetivos = @(Get-CimInstance Win32_Process | Where-Object {
        $cl = $_.CommandLine
        if (-not $cl -or $_.ProcessId -eq $PID) { return $false }
        $cl = $cl.ToLowerInvariant()
        (($cl.Contains($pyMin)) -and $cl.Contains("main.py")) -or $cl.Contains($ps1Min)
    })
    # Primero los run.ps1, para que ninguno relance el python mientras se termina.
    $objetivos = @($objetivos | Sort-Object { if ($_.CommandLine.ToLowerInvariant().Contains($ps1Min)) { 0 } else { 1 } })
    function Matar-Arbol([int]$id) {
        Get-CimInstance Win32_Process -Filter "ParentProcessId=$id" | ForEach-Object { Matar-Arbol $_.ProcessId }
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    }
    foreach ($p in $objetivos) { Matar-Arbol $p.ProcessId }
    Write-Host "Procesos del puente terminados: $($objetivos.Count) (raíces)."
    return
}
if (-Not (Test-Path ".\.venv\Scripts\python.exe")) { throw "No existe .venv. Ejecuta antes .\install.ps1" }
if (-Not (Test-Path ".env")) { throw "No existe .env. Ejecuta antes .\install.ps1 y rellénalo." }

$usuario = "$env:USERDOMAIN\$env:USERNAME"
$script = Join-Path $PSScriptRoot "run.ps1"
$accion = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Log" `
    -WorkingDirectory $PSScriptRoot
$disparador = New-ScheduledTaskTrigger -AtLogOn -User $usuario
# run.ps1 ya reinicia el puente si se cae; esto cubre que se caiga el propio PowerShell.
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $Nombre -Action $accion -Trigger $disparador -Settings $ajustes `
    -Principal $principal -Description "Puente Telegram - Claude Code (claude-voice-swarm)" -Force | Out-Null
Write-Host "Tarea $Nombre registrada: arrancará al iniciar sesión $usuario." -ForegroundColor Green
Write-Host "Arrancar ahora: Start-ScheduledTask -TaskName $Nombre ; log en datos\puente.log"
