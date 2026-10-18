# Claude Voice Swarm

Tú eres el **jefe**. Hablas por **Telegram** (voz o texto) con una sola persona: el **subjefe**. El subjefe no hace trabajo; da órdenes a las **sesiones** de Claude Code que tienes en tus carpetas (en el código se llaman "empresas"), que lo hacen y le informan a él. Solo el subjefe te habla.

```
Tú (jefe)  ⇄  Telegram / ntfy  ⇄  Subjefe  ⇄  sesión "codigo"        (C:\proyectos)
                                           ⇄  sesión "investigacion" (C:\investigacion)
                                           ⇄  ...las que le pidas crear
```

- **El puente es una capa por encima de sesiones normales de Claude Code.** Cada sesión trabaja en su carpeta con su propia configuración (settings de usuario, de proyecto y locales) y tiene memoria persistente: el primer turno crea la sesión y los siguientes la reanudan.
- **El subjefe** (Haiku por defecto) no tiene herramientas de trabajo. Puede `encargar` órdenes, `crear_sesion` cuando se lo pides, `listar_sesiones`, consultar `estado_empresas` y lanzar una `alerta_urgente` a tu móvil.
- **Cada sesión** hace el trabajo como quiera. Su respuesta final va al subjefe. Si necesita avisar a mitad de un trabajo largo, usa `informar_al_subjefe`.
- **Voz local:** Faster-Whisper transcribe tus notas de voz y Kokoro-82M pone voz a las respuestas del subjefe. Si le hablas, te contesta con voz; si le escribes, con texto.
- **Buzón en disco (SQLite, en `datos/`):** todos los mensajes pasan por él. Si reinicias, los mensajes pendientes o a medias vuelven a la cola.

## Instalación en Windows (camino principal)

Requisitos:
- [Claude Code](https://code.claude.com) con el instalador nativo (`claude.exe`; la instalación por npm, `claude.cmd`, no sirve) y con sesión iniciada (ejecuta `claude` una vez).
- Python 3.11 (o 3.12) con el lanzador `py`. kokoro no funciona en 3.13+.
- `ffmpeg` en el PATH, solo para enviar notas de voz (`winget install --id=Gyan.FFmpeg -e`).

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1          # venv con py -3.11 y torch solo CPU; se para ante cualquier error
.\run.ps1              # prueba manual; Ctrl+C para parar
```

`install.ps1 -TorchCuda` instala torch desde PyPI en lugar del índice CPU de PyTorch.

### Arranque automático

```powershell
.\registrar-tarea.ps1               # tarea programada "ClaudeVoiceSwarm" al iniciar sesión, con tu usuario
Start-ScheduledTask -TaskName ClaudeVoiceSwarm
Get-Content datos\puente.log -Wait  # log
.\registrar-tarea.ps1 -Quitar       # eliminarla
```

La tarea corre con tu usuario porque la sesión de Claude Code es tuya (con la cuenta SYSTEM no habría login). `run.ps1` vuelve a lanzar el puente si se cae, con espera creciente, y fija `PYTHONUTF8=1`.

### Primer arranque

1. Crea un bot con [@BotFather](https://t.me/BotFather) y pon el token en `TELEGRAM_TOKEN` de `.env`.
2. Si no sabes tu ID de chat, deja `AUTHORIZED_CHAT_ID=0`, arranca y escribe al bot: te responderá con tu ID. Ponlo en `.env` y reinicia.
   **Mientras sea 0, el bot no obedece a nadie.**
3. Da de alta tus sesiones: en `empresas.json` (opcional, a partir de `empresas.json.example`) o pidiéndoselo al subjefe ("crea una sesión `facturas` en `C:\facturas` para…").
4. Si quieres topes de gasto, ponlos en `.env` (ver "Gasto").
5. Instala la app **ntfy** en Android, pulsa **+** y suscríbete al tema `NTFY_TOPIC` de tu `.env` (servidor `ntfy.sh`). En la app, activa que ese tema se salte **No molestar**. En los ajustes de batería de Android, pon ntfy como "Sin restricciones".
6. Escribe `/probar_alerta` al bot para comprobar que llega.

El tema de ntfy funciona como contraseña: quien lo conozca puede leer y publicar en él. El instalador genera uno aleatorio.

La primera nota de voz descarga los modelos de Whisper y Kokoro de Hugging Face, así que tarda y necesita conexión.

## Sesiones

Hay dos maneras de tener sesiones, y se suman:

- **`empresas.json`** (opcional): sesiones fijas.
- **`crear_sesion`**: le pides al subjefe que cree una (nombre, carpeta, descripción y, si quieres, modelo). Crea la carpeta si no existe, la registra en el buzón (persiste entre reinicios) y queda lista para `encargar`. Te confirma en una frase qué ha creado y dónde.

```json
{
  "codigo": {
    "descripcion": "Desarrollo de software: repositorios, programación, revisiones de código.",
    "directorio": "~/proyectos"
  }
}
```

| Campo | Qué es |
|---|---|
| `directorio` | **Obligatorio.** Carpeta de la sesión, absoluta o con `~` |
| `descripcion` | Lo que lee el subjefe para decidir a quién encargar cada cosa |
| `modelo` | Opcional. Sustituye al modelo por defecto de esa sesión (`--model`) |
| `permission_mode` | Opcional. Sustituye al modo de permisos de sus settings (`--permission-mode`) |
| `allowed_tools` | Opcional. Reglas de permisos que se añaden (`--allowedTools`) |
| `setting_sources` | Opcional. Qué settings carga (`--setting-sources`, p. ej. `project,local`) |
| `max_budget_usd` | Opcional. Gasto máximo por turno; 0 = sin tope. Si falta, se usa `EMPRESA_MAX_BUDGET_USD` |

Los nombres solo admiten `a-z`, `0-9`, `_` y `-`; `jefe`, `subjefe` y `sistema` están reservados. Si un nombre está a la vez en `empresas.json` y entre las creadas, manda `empresas.json`. Los cambios en `empresas.json` se leen al reiniciar el puente; las sesiones creadas con `crear_sesion` están disponibles al momento.

## Permisos

**Los permisos se gestionan en cada carpeta**, como en cualquier sesión de Claude Code: `.claude/settings.json` y `.claude/settings.local.json` de la carpeta de la sesión, más tus settings de usuario (`~/.claude/settings.json`). El puente no los impone ni los valida. Por defecto no pasa `--permission-mode`, `--setting-sources` ni reglas de permisos propias; solo añade `--allowedTools mcp__buzon` para que la sesión pueda usar la herramienta del puente (`informar_al_subjefe`), y las sustituciones que pongas en `empresas.json`.

Las sesiones trabajan sin nadie delante (`claude -p`): lo que sus settings no permitan se deniega y la sesión se lo dirá al subjefe. Para que una sesión pueda hacer más, amplía los permisos en los settings de su carpeta.

Lo que sí hace el puente:

- **Carpeta del puente.** Ninguna sesión puede tener como carpeta la del propio puente ni la de `datos/` (ni una subcarpeta suya). Ahí están el código que ejecuta el subjefe, el buzón y `.env`.
- **Secretos.** Las sesiones heredan tu entorno, salvo las variables definidas en `.env` y cualquier `TELEGRAM_*` o `NTFY_*`: ni el token del bot ni la configuración de ntfy llegan a las sesiones ni a sus comandos. El servidor MCP de las sesiones no carga `.env`.
- **Origen de los mensajes.** Lo fija el puente, no el texto. Los mensajes del jefe los firma el puente con una clave que solo vive en su memoria; una fila que diga venir del jefe sin firma válida se le presenta al subjefe como `no_verificado`. Cada mensaje llega al subjefe dentro de un bloque delimitado por un código aleatorio por turno, y los informes de las sesiones se marcan como información no confiable: un `[JEFE]` dentro de un informe se reescribe como `[jefe-citado]` y no es una orden. El servidor MCP de una sesión solo escribe con el nombre de esa sesión.
- **`crear_sesion` solo con orden del jefe.** El puente habilita la herramienta únicamente en los turnos del subjefe que contienen un mensaje tuyo con firma válida. Un informe de una sesión, por sí solo, no puede hacer que se cree otra.
- **Configuración del subjefe.** El subjefe es del puente: corre sin herramientas propias, con `--strict-mcp-config`, sin settings de usuario y en modo `dontAsk`.

Ten en cuenta que una sesión con permisos amplios (por ejemplo `Bash(python:*)` o `bypassPermissions` en sus settings) puede hacer lo mismo que tú con tu usuario de Windows, incluido leer `.env` o escribir en el buzón. El puente no lo impide; eso se decide en los permisos de cada carpeta. La raíz de confianza es tu cuenta de Telegram: activa la verificación en dos pasos.

## Gasto

Todo es opcional y está desactivado por defecto (vacío o `0` = sin tope). Las cifras son el `total_cost_usd` que declara Claude Code al final de cada turno.

| Variable de `.env` | Qué limita |
|---|---|
| `SUBJEFE_MAX_BUDGET_USD` | Gasto por turno del subjefe (`--max-budget-usd`) |
| `EMPRESA_MAX_BUDGET_USD` | Gasto por turno de cada sesión que no fije su `max_budget_usd` |
| `TOPE_DIARIO_USD` | Suma de todas las sesiones en el día local. Al alcanzarlo, las órdenes quedan en cola hasta el día siguiente y recibes un aviso |
| `MAX_REBOTES` | Encargos encadenados subjefe → sesión → subjefe sin un mensaje tuyo. Por defecto, 3; `0` = sin límite. Al llegar al límite, `encargar` se niega y el subjefe te pregunta antes de seguir |

`/estado` muestra el gasto del día.

## Comandos del bot

| Comando | Qué hace |
|---|---|
| `/start` | Comprueba que estás autorizado |
| `/estado` | Qué sesiones están trabajando, cuántos mensajes tienen en cola y gasto de hoy |
| `/probar_alerta` | Envía una alerta de prueba a ntfy |
| `/nueva_sesion <nombre>` | Olvida la conversación del subjefe o de una sesión; el siguiente turno empieza de cero |
| `/borrar_sesion <nombre>` | Quita del registro una sesión creada con `crear_sesion`. No borra su carpeta. Las de `empresas.json` se quitan editando ese fichero |

## Cómo funciona por dentro

- `main.py`: el puente. Recibe Telegram, transcribe la voz, mete los mensajes en el buzón y, por cada participante con mensajes pendientes, lanza un turno de su sesión de Claude Code. Lo que responde el subjefe se te envía a ti; lo que responde una sesión se le envía al subjefe.
- `sesiones.py`: lanza `claude -p --session-id/--resume` con el entorno filtrado, el prompt de rol y las sustituciones de cada sesión; compone el turno del subjefe con los bloques delimitados.
- `mcp_server.py` y `herramientas.py`: el servidor MCP que da a cada sesión sus herramientas del buzón, según su rol.
- `empresas.py`: lee `empresas.json` y las sesiones registradas.
- `buzon.py`: el buzón SQLite, compartido por el puente y los servidores MCP. Guarda también las sesiones, las sesiones creadas, el gasto y las alertas.

Cada sesión procesa un turno a la vez. Lo que le llegue mientras trabaja se le entrega junto en el siguiente turno. Las sesiones trabajan en paralelo entre ellas.

Si el subjefe no tiene nada que decirte en un turno (por ejemplo, un aviso sin importancia de una sesión), responde exactamente `[SILENCIO]` y no te llega nada.

Si el primer turno de una sesión falla, el siguiente empieza con una sesión nueva. Si una sesión ya en uso falla `SESION_MAX_FALLOS` veces seguidas, se recrea (pierde su memoria) y el subjefe recibe el aviso.

## Opción secundaria: Linux o WSL2

`install.sh` (Python 3.10–3.12; `TORCH_CPU=1 ./install.sh` para torch sin CUDA) y `run.sh` sirven en Linux y macOS. Para dejarlo como servicio hay una unidad systemd en `deploy/claude-voice-swarm.service` (sustituye `USUARIO` y `RUTA`, cópiala a `/etc/systemd/system/` y `systemctl enable --now claude-voice-swarm`).

En WSL2: Claude Code tiene que estar instalado e iniciado **dentro** de la distro (no el de Windows), el repositorio en el disco de Linux (no en `/mnt/c`) y systemd activado en `/etc/wsl.conf` (`[boot]` `systemd=true`). Si quieres que las sesiones no vean tus ficheros de Windows ni puedan lanzar `.exe`, añade `[automount] enabled=false` e `[interop] enabled=false` (afecta a toda la distro). WSL puede apagar la distro cuando no queda ninguna terminal abierta; compruébalo con `wsl -l -v`.

## Pruebas

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install pytest python-dotenv
.venv\Scripts\python.exe -m pytest -q tests
```

Usan un `claude` falso (`tests/claude_falso.py`); no llaman a la API ni a Telegram.

## Limitaciones conocidas

- Si el puente se apaga en mitad de un turno, ese turno se repite al arrancar: la sesión recibe de nuevo la misma orden.
- Los mensajes del jefe que quedaron en cola antes de un reinicio llegan al subjefe como `no_verificado` (la clave de firma cambia en cada arranque); el subjefe debe pedirte confirmación, y en ese turno no puede crear sesiones.
- El tope diario cuenta el coste que declara Claude Code al final de cada turno. Un turno cortado por timeout no declara coste, y varias sesiones en paralelo pueden pasar el tope.
- Un fallo transitorio (sobrecarga, límite de uso, red) no se reintenta: el mensaje se da por procesado y el subjefe o tú recibís el error.
- `/borrar_sesion` no borra la memoria de conversación de esa sesión: si después creas otra con el mismo nombre, reanuda la conversación anterior, si quieres empezar de cero, usa `/nueva_sesion` antes de borrarla.
- Kokoro y Whisper se cargan la primera vez que hacen falta y se quedan en memoria (~1–2 GB según el modelo de Whisper) [ESTIMACIÓN SIN FUENTE].
