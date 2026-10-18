# Registra a nivel de usuario el servidor MCP "subjefe" (mcp_server.py con ROL=externa).
# Todas tus sesiones de Claude Code (app de escritorio, terminal) tendran la herramienta
# mcp__subjefe__informar_al_subjefe. El primer informe de una sesion la da de alta en el
# buzon con su carpeta y su conversacion, para que el subjefe pueda encargarle trabajo.
# En las sesiones que lanza el propio puente el servidor no ofrece ninguna herramienta.
#   .\registrar-mcp-usuario.ps1            registra (o sustituye) el servidor
#   .\registrar-mcp-usuario.ps1 -Quitar    lo quita
# Comprobar: claude mcp get subjefe
param([switch]$Quitar, [string]$Nombre = "subjefe")
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Quitar-Servidor {
    # Si no existe, claude mcp remove falla: no es un error para este script.
    $previo = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & claude mcp remove --scope user $Nombre *> $null
    $codigo = $LASTEXITCODE
    $ErrorActionPreference = $previo
    return $codigo
}

if ($Quitar) {
    if ((Quitar-Servidor) -eq 0) { Write-Host "Servidor MCP '$Nombre' quitado del ambito de usuario." }
    else { Write-Host "No habia ningun servidor MCP '$Nombre' en el ambito de usuario." }
    return
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-Not (Test-Path $python)) { throw "No existe .venv. Ejecuta antes .\install.ps1" }
if (-Not (Get-Command claude -ErrorAction SilentlyContinue)) { throw "No encuentro 'claude' en el PATH." }

# Las rutas se resuelven como lo hace el puente (config.py lee .env). El servidor externo no
# carga .env (no necesita secretos): se le pasan ya resueltas.
$codigo = "import json; from config import Config; print(json.dumps([str(Config.DATA_DIR), str(Config.EMPRESAS_FILE), str(Config.SUBJEFE_DIR), str(Config.JEFE_PC_DIR)]))"
$rutas = (& $python -c $codigo) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $rutas.Count -ne 4) { throw "No se pudieron leer las rutas de config.py" }
$servidor = Join-Path $PSScriptRoot "mcp_server.py"

Quitar-Servidor | Out-Null
# --transport va entre los -e y el nombre: si el nombre sigue a -e, claude lo toma por otro KEY=valor.
& claude mcp add --scope user `
    -e "ROL=externa" -e "DATA_DIR=$($rutas[0])" -e "EMPRESAS_FILE=$($rutas[1])" -e "SUBJEFE_DIR=$($rutas[2])" -e "JEFE_PC_DIR=$($rutas[3])" `
    -e "PYTHONUTF8=1" --transport stdio $Nombre -- $python $servidor
if ($LASTEXITCODE -ne 0) { throw "claude mcp add ha fallado (codigo $LASTEXITCODE)" }

Write-Host "Servidor MCP '$Nombre' registrado a nivel de usuario." -ForegroundColor Green
Write-Host "Buzon: $($rutas[0]). Abre una sesion nueva (las abiertas no lo cargan) y dile: informa de todo al subjefe."
