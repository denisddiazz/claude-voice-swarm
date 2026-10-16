"""Servidor MCP para que Claude Code / Claude Desktop te avise por Telegram o ntfy."""
import asyncio
import contextlib
import sys

from mcp.server.mcpserver import MCPServer

from comunicaciones import enviar_audio_telegram, enviar_texto_telegram
from notificaciones import enviar_alerta_ntfy

mcp = MCPServer("claude-voice-comms")


@mcp.tool()
async def send_telegram_text(mensaje: str) -> str:
    """Envía un mensaje de texto al Telegram del usuario."""
    await enviar_texto_telegram(mensaje)
    return "Mensaje de texto enviado a Telegram."


@mcp.tool()
async def send_telegram_voice(mensaje: str) -> str:
    """Envía una nota de voz (Kokoro TTS, en español) al Telegram del usuario."""
    # stdout es el canal MCP: cualquier print de las librerías de audio va a stderr.
    with contextlib.redirect_stdout(sys.stderr):
        await enviar_audio_telegram(mensaje)
    return "Nota de voz enviada a Telegram."


@mcp.tool()
async def send_urgent_alert(mensaje: str, titulo: str = "Alerta de Claude") -> str:
    """Alerta crítica al móvil del usuario vía ntfy (prioridad máxima) y copia por Telegram.
    Úsala solo para cosas que requieren atención inmediata."""
    ok = await enviar_alerta_ntfy(mensaje, titulo=titulo)
    await enviar_texto_telegram(f"Alerta: {mensaje}")
    return "Alerta enviada." if ok else "No se pudo enviar a ntfy; se envió solo por Telegram."


if __name__ == "__main__":
    mcp.run()
