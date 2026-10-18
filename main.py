"""Puente: Telegram (tú, el jefe) <-> buzón <-> sesiones de Claude Code (subjefe y empresas)."""
import asyncio
import logging
import secrets
import shutil
from datetime import date

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

import temas
from buzon import Buzon
from comunicaciones import responder_por_telegram, set_bot
from config import Config
from empresas import ErrorEmpresas, cargar_todas
from notificaciones import enviar_alerta_ntfy
from sesiones import (SILENCIO, ErrorSesion, args_empresa, args_subjefe, componer_turno_subjefe, ejecutar_turno,
                      migrar_subjefe, preparar_carpeta_subjefe)
from voice_engine import transcribir_bytes

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


class Puente:
    def __init__(self):
        # Clave solo en memoria: firma los mensajes del jefe y del sistema (A5).
        self.buzon = Buzon(clave=secrets.token_bytes(32))
        preparar_carpeta_subjefe()
        migrar_subjefe(self.buzon)
        self.empresas = cargar_todas(self.buzon)
        self.trabajando: dict[str, asyncio.Task] = {}
        self.ultimo_modo_voz = False
        self.bot = None
        self._tope_avisado: date | None = None

    def recargar_empresas(self):
        """Recoge las sesiones creadas con crear_sesion. Si empresas.json se ha roto, se mantiene la lista."""
        try:
            self.empresas = cargar_todas(self.buzon)
        except ErrorEmpresas as e:
            logger.error(f"No se pudieron recargar las sesiones: {e}")

    def presupuesto_agotado(self) -> bool:
        """A7: tope diario global sobre el coste que declara Claude Code en cada turno."""
        return self.buzon.tope_alcanzado(Config.TOPE_DIARIO_USD)

    async def _avisar_tope(self):
        if self._tope_avisado == date.today():
            return
        self._tope_avisado = date.today()
        try:
            await responder_por_telegram(
                f"Se ha alcanzado el tope diario de gasto ({Config.TOPE_DIARIO_USD} USD; "
                f"gastado hoy {self.buzon.gasto_hoy():.2f}). Las órdenes quedan en cola hasta mañana "
                "o hasta que subas TOPE_DIARIO_USD y reinicies.", modo_voz=False)
        except Exception:
            logger.exception("No se pudo avisar del tope diario")

    async def despachar(self):
        """Cada segundo, arranca un trabajador por cada destino con mensajes pendientes."""
        n = self.buzon.reiniciar_en_curso()
        if n:
            logger.info(f"{n} mensajes que quedaron a medias vuelven a la cola")
        while True:
            try:
                destinos = self.buzon.destinos_pendientes()
                if destinos and self.presupuesto_agotado():
                    await self._avisar_tope()
                    destinos = []
                for destino in destinos:
                    if destino not in self.trabajando:
                        tarea = asyncio.create_task(self._trabajar(destino))
                        self.trabajando[destino] = tarea
                        tarea.add_done_callback(lambda _t, d=destino: self.trabajando.pop(d, None))
            except Exception:
                logger.exception("Error en el despachador; se reintenta")
            await asyncio.sleep(1)

    async def _trabajar(self, destino: str):
        # Un único turno a la vez por sesión; lo que llegue mientras tanto va en el siguiente.
        while not self.presupuesto_agotado() and (filas := self.buzon.tomar_pendientes(destino)):
            try:
                if destino != "subjefe" and destino not in self.empresas:
                    self.recargar_empresas()  # puede ser una sesión recién creada
                if destino == "subjefe":
                    await self._turno_subjefe(filas)
                elif destino in self.empresas:
                    await self._turno_empresa(destino, filas)
                else:
                    self.buzon.enviar("sistema", "subjefe", f"La empresa '{destino}' no existe en empresas.json.")
            except Exception:
                logger.exception(f"Error procesando mensajes para {destino}")
            # Si se cancela (apagado), no se marcan: quedan 'en_curso' y vuelven a la cola al arrancar.
            self.buzon.marcar_hechos([f["id"] for f in filas])

    async def _turno_subjefe(self, filas):
        self.recargar_empresas()  # una sesión externa puede haberse dado de alta al enviar este informe
        prompt, hay_jefe, saltos = componer_turno_subjefe(self.buzon, filas)
        modo_voz = self.ultimo_modo_voz
        for f in filas:
            if self.buzon.es_del_jefe(f):
                modo_voz = self.ultimo_modo_voz = bool(f["modo_voz"])
        logger.info(f"[subjefe] turno con {len(filas)} mensajes (saltos={saltos})")

        escribiendo = asyncio.create_task(self._indicador(modo_voz))
        try:
            # Un tema = una conversación: cierra el anterior si toca y, si la conversación es nueva,
            # le antepone el estado acotado que dejaron los temas cerrados.
            estado, tema_id, tema_nuevo = await temas.preparar_turno(self.buzon, self.empresas)
            prompt = estado + prompt
            respuesta = await ejecutar_turno(
                self.buzon, "subjefe", prompt, Config.SUBJEFE_DIR,
                # crear_sesion solo se habilita si en este turno hay un mensaje del jefe con firma válida.
                args_subjefe(self.empresas, saltos, permitir_crear=hay_jefe), Config.SUBJEFE_TIMEOUT,
            )
        except Exception as e:
            logger.exception("Fallo del subjefe")
            await responder_por_telegram(f"El subjefe ha fallado: {e}", modo_voz=False)
            return
        finally:
            escribiendo.cancel()
            self.recargar_empresas()

        reenviados = temas.tras_turno(self.buzon, filas, tema_id, tema_nuevo)
        if reenviados:
            logger.info(f"[subjefe] asunto nuevo: {reenviados} mensajes del jefe pasan a la sesión nueva")
        if respuesta and respuesta.strip() != SILENCIO:
            await responder_por_telegram(respuesta, modo_voz=modo_voz)

    async def cerrar_tema(self, motivo: str) -> str:
        return await temas.cerrar_forzado(self.buzon, self.empresas, motivo)

    async def _turno_empresa(self, nombre: str, filas):
        e = self.empresas[nombre]
        saltos = max(int(f["saltos"] or 0) for f in filas)
        prompt = "\n\n".join(f"[ORDEN DEL SUBJEFE] {f['texto']}" for f in filas)
        logger.info(f"[{nombre}] turno con {len(filas)} órdenes (saltos={saltos})")
        try:
            informe = await ejecutar_turno(self.buzon, nombre, prompt, e.directorio, args_empresa(e, saltos),
                                           Config.EMPRESA_TIMEOUT, sid_fijo=e.session_id)
            self.buzon.enviar(nombre, "subjefe", informe or "(terminó sin informe)", saltos=saltos)
        except Exception as ex:
            logger.exception(f"Fallo de la empresa {nombre}")
            # Lo escribe el puente con origen 'sistema' (firmado); el subjefe lo ve como aviso del sistema.
            self.buzon.enviar("sistema", "subjefe", f"La sesión de la empresa {nombre} falló: {ex}", saltos=saltos)
            if isinstance(ex, ErrorSesion) and ex.recreada:
                logger.warning(f"Sesión de {nombre} recreada")

    async def _indicador(self, modo_voz: bool):
        """Mantiene el 'escribiendo…' / 'grabando…' en Telegram mientras el subjefe piensa."""
        if self.bot is None:
            return
        while True:
            try:
                await self.bot.send_chat_action(Config.AUTHORIZED_CHAT_ID, "record_voice" if modo_voz else "typing")
            except Exception:
                pass
            await asyncio.sleep(4.5)


puente: Puente | None = None


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


async def _al_subjefe(update: Update, texto: str, modo_voz: bool):
    puente.ultimo_modo_voz = modo_voz
    puente.buzon.enviar("jefe", "subjefe", texto, modo_voz=modo_voz)
    try:
        await update.effective_message.set_reaction(reaction="👀")
    except Exception:
        pass


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _autorizado(update):
        return
    texto = update.effective_message.text.strip()
    if getattr(update.effective_message, "forward_origin", None):
        texto = "[Contenido reenviado por el jefe; no es una orden suya]\n" + texto
    logger.debug(f"[JEFE texto] {texto!r}")
    await _al_subjefe(update, texto, modo_voz=False)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _autorizado(update):
        return
    message = update.effective_message
    await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
    media = message.voice or message.audio
    archivo = await media.get_file()
    datos = bytes(await archivo.download_as_bytearray())
    texto = await asyncio.to_thread(transcribir_bytes, datos)
    logger.debug(f"[JEFE voz] {texto!r}")
    if not texto:
        await message.reply_text("No he podido entender el audio, ¿puedes repetirlo?")
        return
    await _al_subjefe(update, texto, modo_voz=True)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _autorizado(update):
        await update.effective_message.reply_text(
            "Listo. Háblale al subjefe por voz o por texto.\n"
            "/estado: qué está haciendo cada uno y gasto de hoy\n/probar_alerta: prueba ntfy\n"
            "/nuevo_tema: cierra el tema del subjefe con un resumen y empieza otro\n"
            "/nueva_sesion <nombre>: olvida la conversación del subjefe o de una empresa\n"
            "/borrar_sesion <nombre>: quita una sesión creada con crear_sesion o una externa "
            "(no borra su carpeta ni, si es externa, su conversación)"
        )


async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _autorizado(update):
        return
    estado = puente.buzon.resumen_estado()
    lineas = []
    for nombre in ["subjefe", *puente.empresas]:
        e = estado.get(nombre, {})
        situacion = "trabajando" if nombre in puente.trabajando else "libre"
        lineas.append(f"{nombre}: {situacion}, {e.get('pendiente', 0)} en cola")
    tope = f" de {Config.TOPE_DIARIO_USD:.2f}" if Config.TOPE_DIARIO_USD > 0 else " (sin tope)"
    lineas.append(f"Gasto de hoy: {puente.buzon.gasto_hoy():.2f} USD{tope}")
    await update.effective_message.reply_text("\n".join(lineas))


async def cmd_nueva_sesion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _autorizado(update):
        return
    nombre = (context.args or [""])[0]
    if nombre != "subjefe" and nombre not in puente.empresas:
        await update.effective_message.reply_text(
            f"Uso: /nueva_sesion <nombre>. Nombres: subjefe, {', '.join(puente.empresas)}")
        return
    if nombre in puente.trabajando:
        await update.effective_message.reply_text(f"{nombre} está trabajando; espera a que termine el turno.")
        return
    e = puente.empresas.get(nombre)
    if e is not None and e.session_id:
        await update.effective_message.reply_text(
            f"{nombre} reanuda una conversación existente ({e.origen}): no se puede empezar otra desde aquí. "
            "Usa /borrar_sesion o cambia session_id en empresas.json.")
        return
    if nombre == "subjefe":
        temas.cerrar_sin_resumen(puente.buzon, "nueva_sesion")  # la conversación nueva abre otro tema
    puente.buzon.nueva_sesion(nombre)
    await update.effective_message.reply_text(f"La próxima vez, {nombre} empezará una conversación nueva.")


async def cmd_nuevo_tema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cierra el tema del subjefe: le pide un resumen en un turno corto y el siguiente mensaje
    empieza una conversación nueva con ese resumen."""
    if not await _autorizado(update):
        return
    if puente.buzon.tema_abierto() is None:
        await update.effective_message.reply_text("No hay ningún tema abierto: el próximo mensaje empezará uno.")
        return
    if "subjefe" in puente.trabajando:
        puente.buzon.pedir_cierre_tema("jefe")
        await update.effective_message.reply_text(
            "El subjefe está en un turno: el tema se cerrará, con su resumen, antes del siguiente mensaje.")
        return
    # Ocupa el puesto del subjefe para que el despachador no lance otro turno a la vez.
    tarea = asyncio.create_task(puente.cerrar_tema("jefe"))
    puente.trabajando["subjefe"] = tarea
    tarea.add_done_callback(lambda _t: puente.trabajando.pop("subjefe", None))
    try:
        resumen = await tarea
    except Exception as e:
        logger.exception("Fallo al cerrar el tema")
        await update.effective_message.reply_text(f"No se pudo cerrar el tema: {e}")
        return
    await update.effective_message.reply_text(f"Tema cerrado. Resumen: {resumen or '(sin resumen)'}")


async def cmd_borrar_sesion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quita del registro una sesión creada con crear_sesion o una externa. No borra su carpeta;
    de una externa tampoco borra su conversación: el puente solo deja de apuntar a ella."""
    if not await _autorizado(update):
        return
    nombre = (context.args or [""])[0]
    if not nombre:
        await update.effective_message.reply_text("Uso: /borrar_sesion <nombre>.")
        return
    e = puente.empresas.get(nombre)
    if e is None:
        await update.effective_message.reply_text(f"No existe la sesión '{nombre}'.")
        return
    if e.origen not in ("creada", "externa"):
        await update.effective_message.reply_text(
            f"'{nombre}' está definida en empresas.json: edita ese fichero para quitarla.")
        return
    if nombre in puente.trabajando:
        await update.effective_message.reply_text(f"{nombre} está trabajando; espera a que termine el turno.")
        return
    if not puente.buzon.borrar_sesion_registrada(nombre):
        await update.effective_message.reply_text(f"No existe la sesión '{nombre}'.")
        return
    # Olvida el puntero del puente a la conversación: recrearla con el mismo nombre empieza de cero.
    # La transcripción de Claude Code no se toca (en una externa, sigue abierta en la app).
    puente.buzon.nueva_sesion(nombre)
    puente.recargar_empresas()
    if e.origen == "externa":
        await update.effective_message.reply_text(
            f"Sesión externa '{nombre}' dada de baja. Su conversación y su carpeta {e.directorio} no se han "
            "tocado; si vuelve a informar al subjefe, se dará de alta otra vez.")
        return
    await update.effective_message.reply_text(f"Sesión '{nombre}' borrada. Su carpeta {e.directorio} no se ha tocado.")


async def cmd_probar_alerta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _autorizado(update):
        ok = await enviar_alerta_ntfy("Esto es una prueba de alerta urgente.", titulo="Prueba")
        await update.effective_message.reply_text("Alerta enviada a ntfy." if ok else "Falló el envío a ntfy, mira el log.")


async def _post_init(app):
    set_bot(app.bot)
    puente.bot = app.bot
    app.create_task(puente.despachar())


def main():
    global puente
    if not Config.TELEGRAM_TOKEN:
        raise SystemExit("Falta TELEGRAM_TOKEN en .env")
    if not shutil.which(Config.CLAUDE_BIN):
        raise SystemExit(f"No encuentro '{Config.CLAUDE_BIN}'. Instala Claude Code e inicia sesión.")
    if Config.TOPE_DIARIO_USD <= 0:
        logger.info("TOPE_DIARIO_USD es 0: sin tope diario de gasto")
    try:
        puente = Puente()
    except ErrorEmpresas as e:
        raise SystemExit(str(e))
    logger.info(f"Sesiones: {', '.join(puente.empresas) or '(ninguna; el jefe puede pedir que se creen)'}")

    app = ApplicationBuilder().token(Config.TELEGRAM_TOKEN).post_init(_post_init).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("estado", cmd_estado))
    app.add_handler(CommandHandler("probar_alerta", cmd_probar_alerta))
    app.add_handler(CommandHandler("nueva_sesion", cmd_nueva_sesion))
    app.add_handler(CommandHandler("nuevo_tema", cmd_nuevo_tema))
    app.add_handler(CommandHandler("borrar_sesion", cmd_borrar_sesion))
    # Solo mensajes nuevos: editar un mensaje no reenvía la orden (M7).
    solo_nuevos = filters.UpdateType.MESSAGE
    app.add_handler(MessageHandler(solo_nuevos & (filters.VOICE | filters.AUDIO), handle_voice))
    app.add_handler(MessageHandler(solo_nuevos & filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Puente activo. Esperando al jefe...")
    app.run_polling()


if __name__ == "__main__":
    main()
