# Claude Voice Swarm

Tú eres el **jefe**. Hablas por **Telegram** (voz o texto) con una sola persona: el **subjefe**. El subjefe no hace trabajo; da órdenes a las **empresas**, que lo hacen y le informan a él. Solo el subjefe te habla.

```
Tú (jefe)  ⇄  Telegram / ntfy  ⇄  Subjefe  ⇄  Empresa "codigo"
                                           ⇄  Empresa "sistemas"
                                           ⇄  Empresa "investigacion"
```

- **Subjefe y empresas son sesiones de Claude Code.** Cada una tiene su propia memoria persistente: el primer turno crea la sesión y los siguientes la reanudan.
- **El subjefe** (Haiku por defecto) no tiene herramientas de trabajo. Solo puede `encargar` órdenes a las empresas, consultar `estado_empresas` y lanzar una `alerta_urgente` a tu móvil.
- **Cada empresa** trabaja en su carpeta, con su modelo y sus permisos. Cómo se organiza por dentro es cosa suya. Su respuesta final va al subjefe. Si necesita avisar a mitad de un trabajo largo, usa `informar_al_subjefe`.
- **Voz local:** Faster-Whisper transcribe tus notas de voz y Kokoro-82M pone voz a las respuestas del subjefe. Si le hablas, te contesta con voz; si le escribes, con texto.
- **Buzón en disco (SQLite):** todos los mensajes pasan por él. Si reinicias, los mensajes pendientes o a medias vuelven a la cola.

## Instalación

Requisitos:
- [Claude Code](https://code.claude.com) instalado y con sesión iniciada (ejecuta `claude` una vez) **en el mismo sistema donde corre el puente**.
- Python 3.10–3.12 (kokoro no funciona en 3.13+), `ffmpeg` y `espeak-ng` (el instalador pone estos dos en Linux y macOS).

El despliegue recomendado es **Linux en WSL2** (ver más abajo). Para probar en Linux o macOS:

```bash
./install.sh                 # TORCH_CPU=1 ./install.sh para torch sin CUDA
./run.sh
```

En Windows (solo pruebas; exige `py -3.12` o `py -3.11` y se para ante cualquier error):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

### Primer arranque

1. Crea un bot con [@BotFather](https://t.me/BotFather) y pon el token en `TELEGRAM_TOKEN`.
2. Si no sabes tu ID de chat, deja `AUTHORIZED_CHAT_ID=0`, arranca y escribe al bot: te responderá con tu ID. Ponlo en `.env` y reinicia.
   **Mientras sea 0, el bot no obedece a nadie.**
3. Edita `empresas.json` (el instalador lo crea a partir de `empresas.json.example`). Crea las carpetas de trabajo.
4. Revisa los topes de gasto de `.env` (`SUBJEFE_MAX_BUDGET_USD`, `TOPE_DIARIO_USD`, `MAX_REBOTES`).
5. Instala la app **ntfy** en Android, pulsa **+** y suscríbete al tema `NTFY_TOPIC` de tu `.env` (servidor `ntfy.sh`). En la app, activa que ese tema se salte **No molestar**. En los ajustes de batería de Android, pon ntfy como "Sin restricciones".
6. Escribe `/probar_alerta` al bot para comprobar que llega.

El tema de ntfy funciona como contraseña: quien lo conozca puede leer y publicar en él. El instalador genera uno aleatorio.

La primera nota de voz descarga los modelos de Whisper y Kokoro de Hugging Face, así que tarda y necesita conexión.

## Empresas (`empresas.json`)

```json
{
  "investigacion": {
    "descripcion": "Buscar información en internet y preparar informes.",
    "directorio": "~/empresas/investigacion",
    "modelo": "opus",
    "permission_mode": "acceptEdits",
    "allowed_tools": ["WebSearch", "WebFetch"],
    "max_budget_usd": 3
  }
}
```

| Campo | Qué es |
|---|---|
| `descripcion` | Lo que lee el subjefe para decidir a quién encargar cada cosa |
| `directorio` | **Obligatorio.** Carpeta dedicada donde trabaja la sesión. Absoluta o con `~` |
| `max_budget_usd` | **Obligatorio.** Gasto máximo por turno (`--max-budget-usd` de Claude Code) |
| `modelo` | Alias o nombre de modelo de Claude Code (`opus`, `sonnet`, `haiku`…). Por defecto `opus` |
| `permission_mode` | `default`, `acceptEdits` (por defecto), `dontAsk`, `plan`, `auto` o `bypassPermissions` |
| `allowed_tools` | Herramientas o comandos que puede usar sin preguntar (reglas de permisos de Claude Code) |
| `restringida` | `true` añade `--restricted`: sin Bash, PowerShell ni WebFetch, y las herramientas de ficheros limitadas a su carpeta |
| `permitir_bypass` | `true` es obligatorio para usar `bypassPermissions` en esa empresa |

Los nombres de empresa solo admiten `a-z`, `0-9`, `_` y `-`; `jefe`, `subjefe` y `sistema` están reservados.

Para añadir una empresa nueva, añádela al JSON y reinicia el puente. Si `empresas.json` no es válido, el puente no arranca y dice por qué.

## Modelo de permisos

Las empresas trabajan sin nadie delante (`claude -p`), así que nadie puede aprobar permisos en el momento. Lo que no esté permitido por `permission_mode` o `allowed_tools` se deniega, y la empresa se lo dirá al subjefe.

Qué protege el puente:

- **Carpetas.** Al arrancar se rechaza cualquier empresa cuya carpeta sea la raíz, tu home o una carpeta que lo contenga, o que contenga el repositorio, `datos/`, `empresas.json` o `.env`, o esté dentro del repositorio o de `datos/`. Con `acceptEdits` la empresa edita sin preguntar todo lo que hay bajo su carpeta; por eso no puede abarcar el código del puente ni su configuración.
- **`bypassPermissions`** solo se acepta con `"permitir_bypass": true` en esa empresa. Úsalo solo con aislamiento a nivel de sistema operativo (ver abajo).
- **Secretos.** Las sesiones de Claude Code reciben solo una lista blanca de variables de entorno (`PATH`, `HOME`, `LANG`…, más las de `ENTORNO_EXTRA`). Nada definido en `.env` ni ninguna variable `TELEGRAM_*` o `NTFY_*` llega a las empresas ni a sus comandos. El servidor MCP de las empresas no carga `.env`.
- **Origen de los mensajes.** Lo fija el puente, no el texto. Los mensajes del jefe los firma el puente con una clave que solo vive en su memoria; una fila que diga venir del jefe sin firma válida se le presenta al subjefe como `no_verificado`. Cada mensaje llega al subjefe dentro de un bloque delimitado por un código aleatorio por turno, y los informes de las empresas se marcan como información no confiable: un `[JEFE]` dentro de un informe se reescribe como `[jefe-citado]` y no es una orden. El servidor MCP de una empresa solo escribe con el nombre de esa empresa, nunca como `jefe`, `subjefe` o `sistema`.
- **Gasto.** Presupuesto por turno obligatorio para empresas y subjefe; tope diario global (`TOPE_DIARIO_USD`) sobre el coste que declara Claude Code; y un máximo de encargos encadenados sin intervención del jefe (`MAX_REBOTES`), a partir del cual `encargar` se niega y el subjefe debe preguntarte.
- **Configuración global.** Las sesiones se lanzan con `--strict-mcp-config` y `--setting-sources` sin `user`: no cargan tus servidores MCP, hooks ni `defaultMode` de `~/.claude/settings.json`.

Qué **no** protege: las reglas de `allowed_tools` que permiten ejecutar un intérprete o una herramienta programable equivalen a ejecución arbitraria con tu usuario. Por ejemplo `Bash(python:*)`, `Bash(node:*)`, `Bash(npm:*)` (scripts de instalación, `npm exec`), `Bash(git:*)` (alias con `!`, `-c core.sshCommand=…`, hooks, y con `acceptEdits` cualquier `git` en un repositorio cuya `.git/config` pueda editar), `Bash(make:*)`, `Bash(docker:*)`, `Bash` o `Bash(*)`. Con una de ellas la empresa puede salir de su carpeta, leer `.env`, escribir en el buzón y usar la red. El puente avisa en el log al cargar reglas así. Usa reglas de comando completas (`Bash(df -h)`) y, para empresas que tengan que ejecutar código, aíslalas a nivel de sistema: un usuario Linux propio, otra distro WSL o un contenedor.

Recomendación: que ninguna empresa lea contenido externo (`WebFetch`, `WebSearch`) y a la vez ejecute código. Una página web puede llevar instrucciones ocultas que acaben en el informe.

La raíz de confianza es tu cuenta de Telegram: activa la verificación en dos pasos.

## Despliegue en WSL2

El puente, Claude Code y las empresas corren dentro de una distro Linux de WSL2, separados del perfil de Windows. Pasos (Ubuntu 24.04, que trae Python 3.12):

1. **Distro.** En PowerShell: `wsl --install -d Ubuntu-24.04`. Se recomienda una distro dedicada al enjambre, porque los ajustes del paso 2 afectan a toda la distro.
2. **`/etc/wsl.conf`** dentro de la distro (con `sudo`):

   ```ini
   [boot]
   systemd=true

   [automount]
   # Sin C:\ montado en /mnt/c: las empresas no ven tus ficheros de Windows.
   enabled=false

   [interop]
   # Sin ejecutar .exe de Windows (cmd.exe, powershell.exe) desde Linux.
   enabled=false
   appendWindowsPath=false
   ```

   Después, en PowerShell: `wsl --shutdown` y vuelve a abrir la distro. Comprueba con `systemctl is-system-running` (vale `running` o `degraded`).
   Sin `automount` ni `interop`, WSL no aísla del todo: la distro sigue teniendo red y comparte el kernel de la VM de WSL con tus otras distros.
3. **Claude Code dentro de WSL**, no el de Windows: instálalo con el instalador nativo de Linux que indique la documentación de Claude Code y ejecuta `claude` una vez para iniciar sesión (sin interop no se abre el navegador de Windows: copia la URL a mano). `command -v claude` debe dar una ruta Linux (`~/.local/bin/claude`), no `/mnt/c/...`.
4. **Repositorio en el disco de Linux** (no en `/mnt/c`):

   ```bash
   git clone <url> ~/claude-voice-swarm && cd ~/claude-voice-swarm
   TORCH_CPU=1 ./install.sh      # o ./install.sh si quieres torch con CUDA
   mkdir -p ~/empresas/{codigo,sistemas,investigacion}
   ./run.sh                      # prueba manual; Ctrl+C para parar
   ```

5. **Servicio systemd:**

   ```bash
   sed -e "s|USUARIO|$USER|g" -e "s|RUTA|$PWD|g" deploy/claude-voice-swarm.service \
     | sudo tee /etc/systemd/system/claude-voice-swarm.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now claude-voice-swarm
   journalctl -u claude-voice-swarm -f
   ```

   El servicio corre con tu usuario (la sesión de Claude Code es tuya), se reinicia si falla, mata las sesiones en curso al pararse y no permite `sudo` (`NoNewPrivileges`).
6. **Que WSL siga encendido.** WSL puede apagar la distro cuando no queda ninguna terminal abierta. Comprueba: cierra todas las terminales de WSL, espera unos minutos y mira `wsl -l -v` en PowerShell. Si aparece `Stopped`, crea en Windows una tarea programada "al iniciar sesión" que ejecute `wsl.exe -d Ubuntu-24.04 --exec sleep infinity` (oculta, con tu usuario). Este paso no está comprobado en esta máquina.

Para aislar más las empresas que ejecutan código, se puede lanzar su sesión con un usuario Linux propio sin acceso al repositorio. No está implementado.

## Comandos del bot

| Comando | Qué hace |
|---|---|
| `/start` | Comprueba que estás autorizado |
| `/estado` | Qué sesiones están trabajando, cuántos mensajes tienen en cola y gasto de hoy |
| `/probar_alerta` | Envía una alerta de prueba a ntfy |
| `/nueva_sesion <nombre>` | Olvida la conversación del subjefe o de una empresa; el siguiente turno empieza de cero |

## Cómo funciona por dentro

- `main.py`: el puente. Recibe Telegram, transcribe la voz, mete los mensajes en el buzón y, por cada participante con mensajes pendientes, lanza un turno de su sesión de Claude Code. Lo que responde el subjefe se te envía a ti; lo que responde una empresa se le envía al subjefe.
- `sesiones.py`: lanza `claude -p --session-id/--resume` con el modelo, los permisos, el presupuesto, el entorno filtrado y el prompt de rol de cada uno; compone el turno del subjefe con los bloques delimitados.
- `mcp_server.py` y `herramientas.py`: el servidor MCP que da a cada sesión sus herramientas del buzón, según su rol.
- `buzon.py`: el buzón SQLite (en `datos/`), compartido por el puente y los servidores MCP. Guarda también las sesiones, el gasto y las alertas.

Cada sesión procesa un turno a la vez. Lo que le llegue mientras trabaja se le entrega junto en el siguiente turno. Las empresas trabajan en paralelo entre ellas.

Si el subjefe no tiene nada que decirte en un turno (por ejemplo, un aviso sin importancia de una empresa), responde exactamente `[SILENCIO]` y no te llega nada.

Si el primer turno de una sesión falla, el siguiente empieza con una sesión nueva. Si una sesión ya en uso falla `SESION_MAX_FALLOS` veces seguidas, se recrea (pierde su memoria) y el subjefe recibe el aviso.

## Pruebas

```bash
python -m venv .venv && .venv/bin/pip install pytest python-dotenv
.venv/bin/python -m pytest -q tests
```

Usan un `claude` falso (`tests/claude_falso.py`); no llaman a la API ni a Telegram.

## Limitaciones conocidas

- Si el puente se apaga en mitad de un turno, ese turno se repite al arrancar: la sesión recibe de nuevo la misma orden.
- Los mensajes del jefe que quedaron en cola antes de un reinicio llegan al subjefe como `no_verificado` (la clave de firma cambia en cada arranque); el subjefe debe pedirte confirmación.
- El tope diario cuenta el coste que declara Claude Code al final de cada turno. Un turno cortado por timeout no declara coste, y varias empresas en paralelo pueden pasar el tope como mucho en la suma de sus presupuestos por turno.
- Un fallo transitorio (sobrecarga, límite de uso, red) no se reintenta: el mensaje se da por procesado y el subjefe o tú recibís el error.
- Kokoro y Whisper se cargan la primera vez que hacen falta y se quedan en memoria (~1–2 GB según el modelo de Whisper) [ESTIMACIÓN SIN FUENTE].
