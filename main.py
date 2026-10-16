import asyncio
import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from comunicaciones import set_bot
from config import Config
from notificaciones import enviar_alerta_ntfy
from orquestador import agente_frontal_recepcionista, tareas_activas
from voice_engine import transcribir_bytes

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def _autorizado(update: Update) -> bool:
    """Deniega por defecto. Si no hay chat configurado, le dice al usuario su ID."""
    chat_id = update.effective_chat.id
    if Config.AUTHORIZED_CHAT_ID == 0:
        await update.effective_message.reply_text(
            f"El bot aún no tiene dueño. Tu ID de chat es {chat_id}. "
            "Ponlo en AUTHORIZED_CHAT_ID dentro de .env y reinicia."
        )
        return False
    if chat_id != Config.AUTHORIZED_CHAT_ID:
        logger.warning(f"Mensaje rechazado del chat {chat_id}")
        return False
    return True


async def _procesar(update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str, modo_voz: bool):
    message = update.effective_message
    try:
        await message.set_reaction(reaction="👀")
    except Exception:
        pass
    await context.bot.send_chat_action(chat_id=message.chat_id, action="record_voice" if modo_voz else "typing")
    await agente_frontal_recepcionista(texto, modo_voz=modo_voz)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _autorizado(update):
        return
    texto = update.effective_message.text.strip()
    logger.info(f"[TEXTO] {texto!r}")
    await _procesar(update, context, texto, modo_voz=False)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _autorizado(update):
        return
    message = update.effective_message
    await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
    media = message.voice or message.audio
    archivo = await media.get_file()
    datos = bytes(await archivo.download_as_bytearray())
    texto = await asyncio.to_thread(transcribir_bytes, datos)
    logger.info(f"[VOZ -> TEXTO] {texto!r}")
    if not texto:
        await message.reply_text("No he podido entender el audio, ¿puedes repetirlo?")
        return
    await _procesar(update, context, texto, modo_voz=True)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _autorizado(update):
        await update.effective_message.reply_text(
            "Listo. Háblame por voz o por texto.\n/estado: tareas en curso\n/probar_alerta: prueba ntfy"
        )


async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _autorizado(update):
        tareas = tareas_activas()
        await update.effective_message.reply_text("\n".join(tareas) if tareas else "No hay tareas en curso.")


async def cmd_probar_alerta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _autorizado(update):
        ok = await enviar_alerta_ntfy("Esto es una prueba de alerta crítica.", titulo="Prueba")
        await update.effective_message.reply_text("Alerta enviada a ntfy." if ok else "Falló el envío a ntfy, mira el log.")


async def _post_init(app):
    set_bot(app.bot)


def main():
    if not Config.TELEGRAM_TOKEN:
        raise SystemExit("Falta TELEGRAM_TOKEN en .env")
    app = ApplicationBuilder().token(Config.TELEGRAM_TOKEN).post_init(_post_init).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("estado", cmd_estado))
    app.add_handler(CommandHandler("probar_alerta", cmd_probar_alerta))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Claude Voice Swarm activo. Esperando mensajes...")
    app.run_polling()


if __name__ == "__main__":
    main()
