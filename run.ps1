# Arranca el puente en Windows y lo vuelve a lanzar si se cae.
#   .\run.ps1          en primer plano, con la salida en la consola
#   .\run.ps1 -Log     salida a datos\puente.log (lo usa la tarea programada)
# Al arrancar termina las instancias previas de ESTE repo (python main.py y otros run.ps1),
# para que no queden dos pollers de Telegram. Al salir, termina el árbol de su hijo.
# Si el propio powershell se mata sin pasar por finally, la limpieza la hace el siguiente arranque
# o .\registrar-tarea.ps1 -Parar.
param([switch]$Log)
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-Not (Test-Path $py)) { throw "No existe .venv. Ejecuta antes .\install.ps1" }
New-Item -ItemType Directory -Force "datos" | Out-Null
$logFile = Join-Path $PSScriptRoot "datos\puente.log"

function Escribir([string]$texto, [string]$color = "Yellow") {
    # UTF-8 sin BOM, sin Add-Content: en Windows PowerShell 5.1 "-Encoding UTF8" mete BOM y mezcla codificaciones.
    if ($Log) { [System.IO.File]::AppendAllText($logFile, $texto + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding $false)) } else { Write-Host $texto -ForegroundColor $color }
}

# Procesos de ESTE repo: el python del venv con main.py, o un run.ps1 de este directorio.
# Se excluye este propio proceso.
function Obtener-Instancias {
    $pyMin = $py.ToLowerInvariant()
    $ps1Min = (Join-Path $PSScriptRoot "run.ps1").ToLowerInvariant()
    Get-CimInstance Win32_Process | Where-Object {
        $cl = $_.CommandLine
        if (-not $cl -or $_.ProcessId -eq $PID) { return $false }
        $cl = $cl.ToLowerInvariant()
        (($cl.Contains($pyMin)) -and $cl.Contains("main.py")) -or $cl.Contains($ps1Min)
    }
}

# Termina un proceso y todos sus descendientes (el venv lanza un python base como hijo).
function Matar-Arbol([int]$id) {
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$id" | ForEach-Object { Matar-Arbol $_.ProcessId }
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
}

function Limpiar-Previos {
    foreach ($p in @(Obtener-Instancias)) {
        Escribir "$(Get-Date -Format s) Termino instancia previa de este repo: pid $($p.ProcessId) $($p.Name)"
        Matar-Arbol $p.ProcessId
    }
}

# Con -Log el hijo es un cmd que redirige a la log; así su pid cubre todo el árbol.
function Lanzar-Hijo {
    if ($Log) {
        $argumentos = '/c ""' + $py + '" main.py >> "' + $logFile + '" 2>&1"'
        return Start-Process -FilePath cmd.exe -ArgumentList $argumentos -WindowStyle Hidden -PassThru
    }
    return Start-Process -FilePath $py -ArgumentList "main.py" -NoNewWindow -PassThru
}

Limpiar-Previos

$espera = 10
$hijo = $null
try {
    while ($true) {
        $inicio = Get-Date
        $hijo = Lanzar-Hijo
        $hijo.WaitForExit()
        $codigo = $hijo.ExitCode
        # Si ha aguantado más de 5 minutos, el fallo no es de arranque: se vuelve a la espera corta.
        if (((Get-Date) - $inicio).TotalMinutes -gt 5) { $espera = 10 } else { $espera = [Math]::Min($espera * 2, 600) }
        Escribir "$(Get-Date -Format s) El puente terminó con código $codigo; reinicio en $espera s"
        Start-Sleep -Seconds $espera
    }
} finally {
    if ($hijo -and -not $hijo.HasExited) {
        Escribir "$(Get-Date -Format s) run.ps1 se detiene: termino el árbol del puente (pid $($hijo.Id))"
        Matar-Arbol $hijo.Id
    }
}
