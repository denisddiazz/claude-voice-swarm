# Claude Voice Swarm

Tú eres el **jefe**. Hablas por **Telegram** (voz o texto) con una sola persona: el **subjefe**. El subjefe no hace trabajo; da órdenes a las **sesiones** de Claude Code que tienes en tus carpetas (en el código se llaman "empresas"), que lo hacen y le informan a él. Solo el subjefe te habla.

```
Tú (jefe)  ⇄  Telegram / ntfy  ⇄  Subjefe  ⇄  sesión "codigo"        (C:\proyectos)
                                           ⇄  sesión "investigacion" (C:\investigacion)
                                           ⇄  ...las que le pidas crear
```

- **El puente es una capa por encima de sesiones normales de Claude Code.** Cada sesión trabaja en su carpeta con su propia configuración (settings de usuario, de proyecto y locales) y tiene memoria persistente: el primer turno crea la sesión y los siguientes la reanudan.
- **El subjefe** (Haiku por defecto) no tiene herramientas de trabajo: solo `Skill`, para cargar sus skills. Trabaja en su carpeta (`SUBJEFE_DIR`, por defecto `C:\Cositas\Subjefe`), cuyo `CLAUDE.md` fija su papel. Puede `encargar` órdenes, `crear_sesion` cuando se lo pides, `listar_sesiones`, consultar `estado_empresas` y lanzar una `alerta_urgente` a tu móvil.
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

Hay tres maneras de tener sesiones, y se suman. `listar_sesiones` muestra el origen de cada una:

- **`empresas.json`** (opcional): sesiones fijas.
- **`crear_sesion`**: le pides al subjefe que cree una (nombre, carpeta, descripción y, si quieres, modelo). Crea la carpeta si no existe, la registra en el buzón (persiste entre reinicios) y queda lista para `encargar`. Te confirma en una frase qué ha creado y dónde.
- **Externas**: sesiones que abres tú en la app y se dan de alta solas al informar al subjefe (ver abajo).

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
| `session_id` | Opcional. UUID de una conversación que ya existe en esa carpeta (alta manual de una sesión tuya): el puente la reanuda con `--resume` en lugar de crear una propia, y nunca la recrea |

Los nombres solo admiten `a-z`, `0-9`, `_` y `-`; `jefe`, `subjefe` y `sistema` están reservados. Si un nombre está a la vez en `empresas.json` y entre las creadas, manda `empresas.json`. Los cambios en `empresas.json` se leen al reiniciar el puente; las sesiones creadas con `crear_sesion` y las externas están disponibles al momento.

### Sesiones externas: "informa de todo al subjefe"

1. Una vez: `.\registrar-mcp-usuario.ps1` registra el MCP `subjefe` a nivel de usuario (`claude mcp add --scope user`; `-Quitar` lo quita). Todas tus sesiones nuevas tendrán `informar_al_subjefe`. En las que lanza el puente no ofrece nada.
2. Abres una sesión normal en la app, en la carpeta del proyecto, y le dices "informa de todo al subjefe".
3. Su primer informe la da de alta en el buzón con origen `externa`: nombre (por defecto, el de la carpeta; la sesión puede pasar otro), carpeta y conversación (`session_id`). Si ya había una externa en esa carpeta, la conversación que informa pasa a ser la principal.
4. Desde entonces el subjefe puede `encargar`le trabajo: el puente reanuda **esa** conversación con `claude -p --resume <session_id>` en su carpeta y le añade el MCP del buzón en ese turno.
5. El `session_id` sale de la variable `CLAUDE_CODE_SESSION_ID` que Claude Code pone en sus procesos hijos (vista en 2.1.296 en los comandos de una sesión de la app; no documentada, y falta confirmar que llega a los servidores MCP), siempre que su transcripción esté en esa carpeta. Si no, se busca en `~/.claude/projects/<carpeta codificada>/` la transcripción reciente que contiene el informe y, si no, la más reciente; en ese último caso la respuesta de la herramienta lo avisa, porque con dos sesiones abiertas en la misma carpeta puede elegir la otra.
6. **Concurrencia:** si la conversación tuvo actividad fuera del puente hace menos de `EXTERNA_MARGEN_MIN` minutos (10 por defecto), el puente no la reanuda: la orden no se entrega y el subjefe recibe el aviso. Si la reanudara con la app abierta, las dos escribirían en la misma conversación y la app no vería el turno del puente.
7. `/borrar_sesion <nombre>` la da de baja sin tocar su conversación ni su carpeta. Si vuelve a informar, se da de alta otra vez.
8. **Caducidad:** si la transcripción `.jsonl` ya no existe (se cerró o archivó en la app) o `claude` responde "No conversation found", la sesión queda `caducada`: `listar_sesiones` lo muestra, no se le encarga nada y el subjefe recibe el aviso. Si su carpeta vuelve a informar desde una conversación, se reactiva con el `session_id` nuevo.

Ninguna sesión, externa o no, puede tener como carpeta la del puente, la de `datos/` o la del subjefe.

### Temas del subjefe

El subjefe no tiene una conversación eterna sino una por tema (lógica en `temas.py`, estado en la tabla `temas` del buzón). Mientras el tema está abierto, cada turno reanuda la misma conversación con `--resume`. Hay un solo tema abierto a la vez y se cierra de cuatro formas:

1. **El subjefe** llama a `cerrar_tema(resumen, pendientes, nuevo_asunto)` cuando das el asunto por bueno o cambias a otro. Con `nuevo_asunto=true` el puente le vuelve a entregar tus mensajes de ese turno en la conversación nueva.
2. **Tú**, con `/nuevo_tema`: el puente pide el resumen al subjefe en un turno corto.
3. **Inactividad**: si pasan `TEMA_INACTIVIDAD_MIN` minutos (120) sin mensajes tuyos, el siguiente mensaje cierra antes el tema anterior con resumen.
4. **Tamaño**: si la transcripción del tema supera `TEMA_MAX_KB` (600), se rota igual y el log lo avisa.

Si en un cierre forzado el subjefe no deja resumen, se guarda uno mínimo que conserva los pendientes anteriores. La conversación siguiente empieza de cero y recibe solo su `CLAUDE.md`, el protocolo y un estado de `TEMA_ESTADO_MAX_CHARS` caracteres (1500) como máximo: los pendientes abiertos, los temas cerrados recientes (fecha, tu encargo abreviado, a qué sesiones se encargó y su resumen; los más antiguos se recortan) y las sesiones disponibles. Así el subjefe sabe a qué se refiere un informe que llega sobre un tema ya cerrado. La primera vez, la conversación anterior a los temas se adopta como tema abierto y se cierra por estas mismas reglas. Modelo y esfuerzo del subjefe: `SUBJEFE_MODEL` (`haiku`) y `SUBJEFE_EFFORT` (`medium`, se pasa como `--effort`).

### Canal PC

Hablas con el subjefe por dos canales: **móvil** (Telegram, lo atiende el subjefe headless) y **PC**: una sesión de la app de escritorio abierta en `SUBJEFE_DIR` (`C:\Cositas\Subjefe`) con el título `SUBJEFE_PC_TITULO` (`Subjefe`; vacío = sin canal PC). En el PC hablas directamente con el subjefe, sin intermediarios: un turno por mensaje. Tiene el mismo `CLAUDE.md` y las mismas skills que el headless, y su `.mcp.json` de proyecto carga `mcp_server.py` con `ROL=subjefe_pc`: las mismas herramientas que el headless más `cambiar_canal` y `ponerse_al_dia`. Ese servidor se niega a arrancar fuera de `SUBJEFE_DIR` o en un turno del puente. El headless no lo carga (`--strict-mcp-config` con su propio `--mcp-config`). Ni uno ni otro se da de alta como sesión externa: el MCP de usuario `subjefe` no ofrece `informar_al_subjefe` en `SUBJEFE_DIR` y `informar_externo` lo rechaza.

El puente escribe al arrancar, conservando lo que ya haya: el `.mcp.json`, `enabledMcpjsonServers: ["buzon"]` y un hook `UserPromptSubmit` en `.claude/settings.json` (`hook_subjefe_pc.py`, sin modelo ni tokens), y `.claude/commands/{movil,pc,canal}.md` si no existen.

- **Detección del PC**: cada vez que escribes en esa sesión, el hook marca canal = PC y anota la actividad. En los turnos headless no hace nada (`CLAUDE_VOICE_SWARM_PUENTE=1`), ni con los mensajes que trae el relé (`<cross-session-message`). `/pc`, `/movil` y `/canal` escritos allí los resuelve el hook sin pasar al modelo (`decision: "block"`; el `reason` lo ve el usuario y no entra en el contexto). Si falla, el hook sale con 0 y no bloquea.
- **Del móvil al PC**: el hook añade al contexto (`additionalContext`, ver https://code.claude.com/docs/en/hooks, apartado UserPromptSubmit) lo pasado por el móvil desde la última vez: tus mensajes por Telegram, los informes recibidos (marcados como no confiables), lo que el subjefe te contestó por Telegram, los temas cerrados y lo que esperaba en la cola, que pasa a tratarse en el PC. En una conversación nueva añade además el protocolo y el estado del tema.
- **Informes con canal PC**: lo que llega para el subjefe va a la sesión del PC por el relé (abajo), no a un turno headless. Si no se entrega, el canal pasa al móvil, el headless trata el informe y la respuesta va por Telegram con el aviso «No pude entregarlo en el PC». El límite de encargos encadenados cuenta los informes reenviados al PC y vuelve a cero cuando escribes; `crear_sesion` solo se ofrece en el PC justo tras un mensaje tuyo.
- **Del PC al móvil** (escribes por Telegram, `/movil`, o `PC_INACTIVIDAD_MIN` minutos, 15, sin teclado ni ratón según `GetLastInputInfo`; con `PC_INACTIVIDAD_FUENTE=mensajes`, sin mensajes tuyos en el PC): llega por Telegram, en un mensaje, lo que el subjefe te dijo en el PC desde tu última actividad allí (de la transcripción de la sesión). El primer turno headless continúa la conversación del PC con `claude -p --resume <id del PC> --fork-session`: «When resuming, create a new session ID instead of reusing the original» (https://code.claude.com/docs/en/cli-reference). La copia hereda todo el contexto y no escribe en la transcripción que la app tiene abierta; su id se toma del `session_id` del resultado, porque combinar `--fork-session` con `--session-id` no está documentado. Si la transcripción no está en la carpeta de proyectos o el fork falla, el turno sigue en la conversación propia con un extracto de lo hablado en el PC. Si el subjefe del PC cerró el tema, no hay fork: empieza un tema nuevo con su resumen. Si no hay tema abierto cuando escribes en el PC, el hook abre uno.
- **Relé**: `claude -p` en `datos/rele-pc`, Haiku, `--effort low`, `--permission-mode bypassPermissions`, `--tools ListAgents,SendMessage` y `--allowedTools ListAgents SendMessage`, `--strict-mcp-config` sin servidores, `--setting-sources project` (la carpeta no tiene settings), `--no-session-persistence`, prompt de sistema propio y presupuesto `RELE_PC_MAX_BUDGET_USD`. Hace falta `bypassPermissions` porque las sesiones de la app corren en ese modo y retienen los mensajes de un remitente con otro modo («retenido — esperando aprobación», observado el 18/10/2026). Por qué es seguro: con solo esas dos herramientas no puede leer, escribir ni ejecutar nada; un hook `PreToolUse` (`guarda_rele.py`, pasado con `--settings`) deniega todo salvo `ListAgents` y un `SendMessage` al título exacto con el texto exacto (lo lee de un fichero que el puente escribe y borra) y sin `notify_when_idle`, y deniega también si falla; el puente comprueba después en la salida `stream-json` que el `SendMessage` fue a ese título, con ese texto y con `success`. La denegación del hook en modo bypass se comprobó en una prueba real el 18/10/2026 (`init` con solo `ListAgents` y `SendMessage`, `permissionMode: bypassPermissions`, envío a otro destinatario denegado); la documentación no la garantiza para ese modo.
- **Urgencias**: `alerta_urgente` suena siempre en el móvil (ntfy); con canal PC se copia también allí. La voz es solo de Telegram.
- **Coste**: el relé costó 0,00147 USD (`total_cost_usd`, prueba del 18/10/2026, `SendMessage` denegado por la guarda) y 0,0016 USD en una entrega fallida con la configuración anterior. Una entrega con éxito no se ha medido `[ESTIMACIÓN SIN FUENTE: del mismo orden]`. Se anota como `rele-pc` y cuenta para `TOPE_DIARIO_USD`. La sesión del PC gasta además un turno de su modelo por cada informe que recibe `[ESTIMACIÓN SIN FUENTE]`.
- **Límites**: `SendMessage` solo llega a sesiones vivas en `ListAgents`; una sesión cerrada o descargada no aparece, y el nombre en `ListAgents` puede no coincidir con el título de la app (se vio el 18/10/2026). Entonces todo cae a Telegram. `GetLastInputInfo` mide cualquier entrada de la sesión de Windows: en una muestra de 10 minutos el máximo sin entrada fue 23,5 s, así que si algo inyecta entrada el PC nunca parece inactivo; para eso está `PC_INACTIVIDAD_FUENTE=mensajes`. Lo que hace el hook cuenta como tuyo, pero cualquier proceso local de tu usuario podría escribir en el buzón, igual que con el resto.

Para usarlo: reinicia el puente (escribe los ficheros de `C:\Cositas\Subjefe`), abre en la app una sesión nueva en esa carpeta, ponle el título `Subjefe`, elige Haiku (`/model haiku`) y aprueba el servidor `buzon` si lo pide.

## Permisos

**Los permisos se gestionan en cada carpeta**, como en cualquier sesión de Claude Code: `.claude/settings.json` y `.claude/settings.local.json` de la carpeta de la sesión, más tus settings de usuario (`~/.claude/settings.json`). El puente no los impone ni los valida. Por defecto no pasa `--permission-mode`, `--setting-sources` ni reglas de permisos propias; solo añade `--allowedTools mcp__buzon` para que la sesión pueda usar la herramienta del puente (`informar_al_subjefe`), y las sustituciones que pongas en `empresas.json`.

Las sesiones trabajan sin nadie delante (`claude -p`): lo que sus settings no permitan se deniega y la sesión se lo dirá al subjefe. Para que una sesión pueda hacer más, amplía los permisos en los settings de su carpeta.

Lo que sí hace el puente:

- **Carpeta del puente.** Ninguna sesión puede tener como carpeta la del propio puente ni la de `datos/` (ni una subcarpeta suya). Ahí están el código que ejecuta el subjefe, el buzón y `.env`.
- **Secretos.** Las sesiones heredan tu entorno, salvo las variables definidas en `.env` y cualquier `TELEGRAM_*` o `NTFY_*`: ni el token del bot ni la configuración de ntfy llegan a las sesiones ni a sus comandos. El servidor MCP de las sesiones no carga `.env`.
- **Origen de los mensajes.** Lo fija el puente, no el texto. Los mensajes del jefe los firma el puente con una clave que solo vive en su memoria; una fila que diga venir del jefe sin firma válida se le presenta al subjefe como `no_verificado`. Cada mensaje llega al subjefe dentro de un bloque delimitado por un código aleatorio por turno, y los informes de las sesiones se marcan como información no confiable: un `[JEFE]` dentro de un informe se reescribe como `[jefe-citado]` y no es una orden. El servidor MCP de una sesión solo escribe con el nombre de esa sesión.
- **`crear_sesion` solo con orden del jefe.** El puente habilita la herramienta únicamente en los turnos del subjefe que contienen un mensaje tuyo con firma válida. Un informe de una sesión, por sí solo, no puede hacer que se cree otra.
- **Configuración del subjefe.** El subjefe es del puente: corre en `SUBJEFE_DIR` solo con la herramienta `Skill` (`--tools Skill`, permitida con `--allowedTools`), con `--strict-mcp-config`, sin settings de usuario (`--setting-sources project`) y en modo `dontAsk`. La documentación dice que las skills de proyecto se cargan desde `.claude/skills` de la carpeta de trabajo y de las superiores, pero no aclara qué excluye `--setting-sources project`; la carga de su `CLAUDE.md` (y del de `C:\Cositas`, que hereda a propósito) y de sus skills está pendiente de comprobar con un turno real. Si `SUBJEFE_DIR` no tiene `CLAUDE.md`, el puente le copia `plantillas/subjefe/CLAUDE.md` al arrancar; nunca sobrescribe uno existente. Sus skills (`traducir-al-jefe`, `traducir-a-sesion`) viven en `C:\Cositas\Skills\subjefe` y `C:\Cositas\Skills\instalar.ps1` las enlaza en `SUBJEFE_DIR\.claude\skills`.
- **Migración a `SUBJEFE_DIR`.** Antes el subjefe trabajaba en `datos/subjefe`. Claude Code guarda las conversaciones por carpeta, así que al primer arranque con la nueva carpeta el subjefe empieza una conversación nueva (lo anota el log). La antigua sigue en `~/.claude/projects/` y no se borra.

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
| `/nuevo_tema` | Cierra el tema del subjefe: le pide un resumen y el siguiente mensaje empieza una conversación nueva con él. Si el subjefe está en un turno, el cierre se hace antes del siguiente mensaje |
| `/nueva_sesion <nombre>` | Olvida la conversación del subjefe o de una sesión; el siguiente turno empieza de cero (en el subjefe, cierra el tema sin resumen). No vale para las que reanudan una conversación existente (externas o con `session_id`) |
| `/borrar_sesion <nombre>` | Quita del registro una sesión creada con `crear_sesion` o una externa. No borra su carpeta ni la conversación de una externa. Las de `empresas.json` se quitan editando ese fichero |
| `/pc`, `/movil`, `/canal` | Fuerzan o consultan el canal por el que te llega lo del subjefe (ver «Canal PC») |

## Cómo funciona por dentro

- `main.py`: el puente. Recibe Telegram, transcribe la voz, mete los mensajes en el buzón y, por cada participante con mensajes pendientes, lanza un turno de su sesión de Claude Code. Lo que responde el subjefe se te envía a ti; lo que responde una sesión se le envía al subjefe.
- `sesiones.py`: lanza `claude -p --session-id/--resume` con el entorno filtrado, el prompt de rol y las sustituciones de cada sesión; compone el turno del subjefe con los bloques delimitados.
- `mcp_server.py` y `herramientas.py`: el servidor MCP que da a cada sesión sus herramientas del buzón, según su rol.
- `empresas.py`: lee `empresas.json` y las sesiones registradas (creadas y externas).
- `externas.py`: identifica la conversación de una sesión externa y detecta si está en uso fuera del puente.
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
- `/borrar_sesion` olvida también la conversación de la sesión: si la recreas con el mismo nombre, empieza de cero. Su carpeta no se toca.
- Sesiones externas: la comprobación de concurrencia solo mira la última modificación de la transcripción. Si escribes en la app mientras el puente está en mitad de un turno, o antes de que pasen los minutos de margen sin actividad, las dos escrituras pueden mezclarse. Si una conversación externa falla al reanudarse, no se recrea: el subjefe recibe el error.
- Los informes de una sesión externa no arrastran encargos encadenados (empiezan en 0), igual que si los iniciara el jefe.
- Kokoro y Whisper se cargan la primera vez que hacen falta y se quedan en memoria (~1–2 GB según el modelo de Whisper) [ESTIMACIÓN SIN FUENTE].
