# Claude Voice Swarm

Asistente personal por **Telegram** (voz o texto) que trabaja en tu máquina con Claude y te avisa al móvil con **ntfy** cuando algo es crítico.

- **Voz local:** Faster-Whisper transcribe tus notas de voz y Kokoro-82M responde con voz. El audio no sale de tu máquina.
- **Simetría de canal:** si hablas, te contesta con voz; si escribes, con texto.
- **Recepcionista (Claude Haiku 5.5):** responde al instante y decide si la petición necesita trabajo de verdad.
- **Director (Claude Sonnet 5.5):** en segundo plano, investiga con herramientas reales (comandos permitidos, lectura de archivos) y emite un resumen.
- **Alertas críticas (ntfy):** si el Director marca el resultado como crítico, recibes una notificación de prioridad máxima en el móvil, además del informe por Telegram.
- **Servidor MCP:** Claude Code o Claude Desktop pueden mandarte mensajes, notas de voz y alertas.

## Instalación

Requisitos: Python 3.10–3.12 (recomendado; Kokoro y Faster-Whisper pueden ir por detrás de la última versión de Python), `ffmpeg` y `espeak-ng` (el instalador los pone en Linux y macOS).

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
3. Instala la app **ntfy** en Android (Google Play o F-Droid), pulsa **+** y suscríbete al tema `NTFY_TOPIC` de tu `.env` (servidor `ntfy.sh`).
4. En la app, abre el tema y activa la opción de saltarse **No molestar** para que las alertas de prioridad 5 suenen siempre. En los ajustes de batería de Android, pon ntfy como "Sin restricciones".
5. Escribe `/probar_alerta` al bot para comprobar que llega.

El tema de ntfy funciona como contraseña: quien lo conozca puede leer y publicar en él. El instalador genera uno aleatorio. Si quieres más control, puedes [alojar ntfy tú mismo](https://docs.ntfy.sh/install/) y usar `NTFY_TOKEN`.

## Comandos del bot

| Comando | Qué hace |
|---|---|
| `/start` | Comprueba que estás autorizado |
| `/estado` | Tareas del Director en curso |
| `/probar_alerta` | Envía una alerta de prueba a ntfy |

## Qué puede hacer el Director

Solo **lectura**, dentro de `WORKDIR`:

- `ejecutar_comando`: solo comandos que empiecen por un prefijo de `ALLOWED_COMMANDS` (por defecto `git status`, `git log`, `df`, `free`, `systemctl status`, `docker ps`…). Se ejecutan sin shell, con límite de 60 s.
- `leer_archivo` y `listar_directorio`: rutas dentro de `WORKDIR`; `.env` y las claves SSH están bloqueadas.

Para ampliar lo que puede hacer, añade prefijos a `ALLOWED_COMMANDS`. Piensa bien antes de añadir comandos que escriban o borren: cualquiera con acceso a tu Telegram podría pedirlos.

## Conectar con Claude Code (MCP)

El instalador imprime el comando exacto. Es algo así:

```bash
claude mcp add voice-comms -- "$(pwd)/venv/bin/python" "$(pwd)/mcp_server.py"
```

Herramientas: `send_telegram_text`, `send_telegram_voice`, `send_urgent_alert`.

## Limitaciones conocidas

- La memoria de conversación y las tareas en curso viven en RAM: se pierden al reiniciar.
- Kokoro y Whisper se cargan la primera vez que hacen falta y se quedan en memoria (~1–2 GB según el modelo de Whisper).
- `DIRECTOR_FALLBACKS=1` usa los reintentos automáticos de la API de Claude cuando Sonnet rechaza una petición. Solo funciona con la API directa de Anthropic; ponlo a `0` si usas otra plataforma.
