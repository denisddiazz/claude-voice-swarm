"""Envío de mensajes al usuario por Telegram (texto o nota de voz)."""
import asyncio
import logging

from telegram import Bot

from config import Config
from voice_engine import texto_a_ogg_telegram

logger = logging.getLogger(__name__)

_bot: Bot | None = None
LIMITE_TEXTO = 4000


def set_bot(bot: Bot):
    """main.py registra aquí el bot de la Application para reutilizarlo."""
    global _bot
    _bot = bot


async def _get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=Config.TELEGRAM_TOKEN)
        await _bot.initialize()
    return _bot


async def enviar_texto_telegram(mensaje: str):
    bot = await _get_bot()
    for i in range(0, len(mensaje), LIMITE_TEXTO):
        await bot.send_message(chat_id=Config.AUTHORIZED_CHAT_ID, text=mensaje[i : i + LIMITE_TEXTO])


async def enviar_audio_telegram(mensaje: str):
    """Sintetiza con Kokoro en un hilo aparte y envía la nota de voz con el texto como pie."""
    bot = await _get_bot()
    ogg = await asyncio.to_thread(texto_a_ogg_telegram, mensaje)
    caption = mensaje if len(mensaje) <= 1000 else mensaje[:997] + "..."
    await bot.send_voice(chat_id=Config.AUTHORIZED_CHAT_ID, voice=ogg, caption=caption)


async def responder_por_telegram(mensaje: str, modo_voz: bool):
    """Simetría de canal: voz -> voz, texto -> texto. Si falla la voz, cae a texto."""
    if modo_voz:
        try:
            await enviar_audio_telegram(mensaje)
            return
        except Exception as e:
            logger.error(f"Fallo al generar/enviar voz, envío texto: {e}")
    await enviar_texto_telegram(mensaje)
