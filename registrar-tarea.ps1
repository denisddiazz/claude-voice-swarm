# Registra una tarea programada que arranca el puente al iniciar sesión, con tu usuario
# (la sesión de Claude Code es tuya: con la cuenta SYSTEM no habría login).
#   .\registrar-tarea.ps1            registra (o sustituye) la tarea
#   .\registrar-tarea.ps1 -Quitar    la elimina
# Gestión: Start-ScheduledTask / Stop-ScheduledTask -TaskName ClaudeVoiceSwarm
param([switch]$Quitar, [string]$Nombre = "ClaudeVoiceSwarm")
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if ($Quitar) {
    Unregister-ScheduledTask -TaskName $Nombre -Confirm:$false
    Write-Host "Tarea $Nombre eliminada."
    return
}
if (-Not (Test-Path ".\venv\Scripts\python.exe")) { throw "No existe venv. Ejecuta antes .\install.ps1" }
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
