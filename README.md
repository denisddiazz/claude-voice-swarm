# Claude Voice Swarm

Tú eres el **jefe**. Hablas por **Telegram** (voz o texto) con una sola persona: el **subjefe**. El subjefe no hace trabajo; da órdenes a las **empresas**, que lo hacen y le informan a él. Solo el subjefe te habla.

```
Tú (jefe)  ⇄  Telegram / ntfy  ⇄  Subjefe  ⇄  Empresa "codigo"
                                           ⇄  Empresa "sistemas"
                                           ⇄  Empresa "investigacion"
```

- **Subjefe y empresas son sesiones de Claude Code.** Cada una tiene su propia memoria persistente: el primer turno crea la sesión y los siguientes la reanudan.
- **El subjefe** (Haiku por defecto) no tiene herramientas de trabajo. Solo puede `encargar` órdenes a las empresas, consultar `estado_empresas` y lanzar una `alerta_urgente` a tu móvil.
- **Cada empresa** trabaja en su carpeta, con su modelo (Opus por defecto) y sus permisos. Cómo se organiza por dentro es cosa suya. Su respuesta final va al subjefe. Si necesita avisar a mitad de un trabajo largo, usa `informar_al_subjefe`.
- **Voz local:** Faster-Whisper transcribe tus notas de voz y Kokoro-82M pone voz a las respuestas del subjefe. Si le hablas, te contesta con voz; si le escribes, con texto.
- **Buzón en disco (SQLite):** todos los mensajes pasan por él. Si reinicias, los mensajes pendientes o a medias vuelven a la cola.

## Instalación

Requisitos:
- [Claude Code](https://code.claude.com) instalado y con sesión iniciada (ejecuta `claude` una vez).
- Python 3.10–3.12, `ffmpeg` y `espeak-ng` (el instalador pone estos dos en Linux y macOS).

```bash
./install.sh   # Linux / WSL2 / macOS
./run.sh
```

En Windows (PowerShell):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

### Primer arranque

1. Crea un bot con [@BotFather](https://t.me/BotFather) y pon el token en `TELEGRAM_TOKEN`.
2. Si no sabes tu ID de chat, deja `AUTHORIZED_CHAT_ID=0`, arranca y escribe al bot: te responderá con tu ID. Ponlo en `.env` y reinicia.
   **Mientras sea 0, el bot no obedece a nadie.**
3. Edita `empresas.json` (el instalador lo crea a partir de `empresas.json.example`).
4. Instala la app **ntfy** en Android, pulsa **+** y suscríbete al tema `NTFY_TOPIC` de tu `.env` (servidor `ntfy.sh`). En la app, activa que ese tema se salte **No molestar**. En los ajustes de batería de Android, pon ntfy como "Sin restricciones".
5. Escribe `/probar_alerta` al bot para comprobar que llega.

El tema de ntfy funciona como contraseña: quien lo conozca puede leer y publicar en él. El instalador genera uno aleatorio.

## Empresas (`empresas.json`)

```json
{
  "codigo": {
    "descripcion": "Desarrollo de software: repositorios, programación, revisiones.",
    "directorio": "~/proyectos",
    "modelo": "opus",
    "permission_mode": "acceptEdits",
    "allowed_tools": ["Bash(git:*)", "Bash(npm:*)"],
    "max_budget_usd": 5
  }
}
```

| Campo | Qué es |
|---|---|
| `descripcion` | Lo que lee el subjefe para decidir a quién encargar cada cosa |
| `directorio` | Carpeta donde trabaja la sesión de Claude Code de la empresa |
| `modelo` | Alias o nombre de modelo de Claude Code (`opus` = el Opus más reciente) |
| `permission_mode` | Modo de permisos de Claude Code: `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`… |
| `allowed_tools` | Herramientas o comandos que puede usar sin preguntar (reglas de permisos de Claude Code) |
| `max_budget_usd` | Opcional: gasto máximo por turno |

Las empresas trabajan sin nadie delante (`claude -p`), así que nadie puede aprobar permisos en el momento. Lo que no esté permitido en `permission_mode` o `allowed_tools` se deniega, y la empresa se lo dirá al subjefe. Para que una empresa pueda hacer más, amplía su `allowed_tools`. `bypassPermissions` le da vía libre total: úsalo solo en carpetas donde no te importe lo que pase.

Para añadir una empresa nueva, añádela al JSON y reinicia el puente.

## Comandos del bot

| Comando | Qué hace |
|---|---|
| `/start` | Comprueba que estás autorizado |
| `/estado` | Qué sesiones están trabajando y cuántos mensajes tienen en cola |
| `/probar_alerta` | Envía una alerta de prueba a ntfy |

## Cómo funciona por dentro

- `main.py`: el puente. Recibe Telegram, transcribe la voz, mete los mensajes en el buzón y, por cada participante con mensajes pendientes, lanza un turno de su sesión de Claude Code. Lo que responde el subjefe se te envía a ti; lo que responde una empresa se le envía al subjefe.
- `sesiones.py`: lanza `claude -p --session-id/--resume` con el modelo, los permisos y el prompt de rol de cada uno.
- `mcp_server.py`: el servidor MCP que da a cada sesión sus herramientas del buzón, según su rol.
- `buzon.py`: el buzón SQLite (en `datos/`), compartido por el puente y los servidores MCP.

Cada sesión procesa un turno a la vez. Lo que le llegue mientras trabaja se le entrega junto en el siguiente turno. Las empresas trabajan en paralelo entre ellas.

Si el subjefe no tiene nada que decirte en un turno (por ejemplo, un aviso sin importancia de una empresa), responde `[SILENCIO]` y no te llega nada.

## Limitaciones conocidas

- Si el puente se apaga en mitad de un turno, ese turno se repite al arrancar: la sesión recibe de nuevo la misma orden.
- Kokoro y Whisper se cargan la primera vez que hacen falta y se quedan en memoria (~1–2 GB según el modelo de Whisper).
