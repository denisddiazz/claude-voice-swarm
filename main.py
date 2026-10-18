"""Puente: Telegram (tú, el jefe) <-> buzón <-> sesiones de Claude Code (subjefe y empresas)."""
import asyncio
import logging
import secrets
import shutil
import time
from datetime import date

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

import herramientas
import rele_pc
import temas
from buzon import Buzon
from comunicaciones import responder_por_telegram, set_bot
from config import Config
from empresas import ErrorEmpresas, cargar_todas
from notificaciones import enviar_alerta_ntfy
from sesiones import (SILENCIO, ErrorSesion, args_empresa, args_subjefe, componer_turno_subjefe, ejecutar_turno,
                      migrar_subjefe, preparar_carpeta_jefe_pc, preparar_carpeta_subjefe)
from voice_engine import transcribir_bytes

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


class Puente:
    def __init__(self):
        # Clave solo en memoria: firma los mensajes del jefe y del sistema (A5).
        self.buzon = Buzon(clave=secrets.token_bytes(32))
        preparar_carpeta_subjefe()
        if Config.JEFE_PC_TITULO:
            preparar_carpeta_jefe_pc()
        migrar_subjefe(self.buzon)
        self.empresas = cargar_todas(self.buzon)
        self.trabajando: dict[str, asyncio.Task] = {}
        self.ultimo_modo_voz = False
        self.bot = None
        self._tope_avisado: date | None = None
        self._entrega = asyncio.Lock()   # una entrega al jefe a la vez (el canal no cambia a mitad)
        self._ultima_vigilancia = 0.0

    # --- entregas al jefe: móvil (Telegram) o PC (ver README, "Canal PC") ---
    @staticmethod
    def canal_pc_configurado() -> bool:
        return bool(Config.JEFE_PC_TITULO)

    def canal_activo(self) -> str:
        return self.buzon.estado_canal()["activo"] if self.canal_pc_configurado() else "movil"

    async def al_jefe(self, partes: list[str], modo_voz: bool = False, urgencias: list[str] = ()):
        """Entrega al jefe por el canal activo. Antepone lo que no vio en el canal anterior, si hay un
        cambio pendiente. En el PC todo va en una sola entrega de texto (un relé por turno); la voz es
        solo de Telegram. Las urgencias ya han sonado en el móvil (ntfy): se copian solo al PC."""
        partes = [p for p in partes if p and p.strip()]
        async with self._entrega:
            no_visto = self.buzon.tomar_no_visto()
            recopilacion = rele_pc.componer_no_visto(*no_visto) if no_visto else ""
            if self.canal_activo() == "pc":
                bloques = [b for b in (recopilacion, *urgencias, *partes) if b]
                if not bloques:
                    return
                r = await rele_pc.entregar(rele_pc.SEPARADOR.join(bloques))
                if r.coste_usd > 0:
                    self.buzon.registrar_gasto("rele-pc", r.coste_usd)
                logger.info(f"[rele-pc] entrega {'ok' if r.ok else 'fallida'}: {len(bloques)} bloques, "
                            f"{sum(map(len, bloques))} caracteres, {r.segundos:.1f}s, coste {r.coste_usd:.4f} USD"
                            f"{'' if r.ok else '; ' + r.motivo}")
                if r.ok:
                    if recopilacion:
                        self.buzon.registrar_salida("pc", recopilacion, recopilacion=True)
                    for i, b in enumerate([*urgencias, *partes]):
                        self.buzon.registrar_salida("pc", b, coste_usd=r.coste_usd if i == 0 else None)
                    return
                # No llegó: pasa al móvil, que recibe lo no visto en el PC más lo de ahora con un aviso.
                # La recopilación que venía del móvil no se repite allí.
                self.buzon.cambiar_canal("movil", "fallo_rele")
                no_visto = self.buzon.tomar_no_visto()
                recopilacion = rele_pc.componer_no_visto(*no_visto) if no_visto else ""
                aviso = f"(No pude entregarlo en el PC: {r.motivo}. Paso al móvil; /pc para volver.)"
                partes = [f"{aviso}\n\n{partes[0]}" if partes else aviso, *partes[1:]]
                modo_voz = False
            if recopilacion:
                await responder_por_telegram(recopilacion, modo_voz=False)
                self.buzon.registrar_salida("movil", recopilacion, recopilacion=True)
            for p in partes:
                await responder_por_telegram(p, modo_voz=modo_voz)
                self.buzon.registrar_salida("movil", p)

    def jefe_por_telegram(self):
        """El jefe ha escrito por Telegram: el canal pasa al móvil."""
        ahora = time.time()
        if self.canal_pc_configurado() and self.buzon.cambiar_canal("movil", "jefe", ahora):
            logger.info("[canal] pasa al móvil: el jefe ha escrito por Telegram")
        self.buzon.anotar_jefe("movil", ahora)

    async def vigilar_canal(self, ahora: float | None = None):
        """Cada pocos segundos: anota la actividad en el PC, pasa al móvil por inactividad y entrega lo
        no visto si nadie más lo va a entregar (si hay un turno del subjefe, va con su respuesta)."""
        if not self.canal_pc_configurado():
            return
        ahora = time.time() if ahora is None else ahora
        inactivo = rele_pc.segundos_inactivo() if Config.PC_INACTIVIDAD_FUENTE != "mensajes" else None
        if inactivo is not None:
            self.buzon.anotar_actividad_pc(ahora - inactivo)
        estado = self.buzon.estado_canal()
        if estado["activo"] == "pc" and Config.PC_INACTIVIDAD_MIN > 0:
            # Sin medida de entrada, cuenta el último mensaje del jefe por el PC (o el paso al PC).
            if ahora - estado["actividad_pc"] >= Config.PC_INACTIVIDAD_MIN * 60:
                self.buzon.cambiar_canal("movil", "inactividad", ahora)
                logger.info(f"[canal] pasa al móvil: {Config.PC_INACTIVIDAD_MIN:g} min sin actividad en el PC"
                            f" ({'entrada de Windows' if inactivo is not None else 'mensajes del jefe'})")
        if (self.buzon.hay_no_visto() and "subjefe" not in self.trabajando
                and "subjefe" not in self.buzon.destinos_pendientes()):
            await self.al_jefe([])

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
            await self.al_jefe([
                f"Se ha alcanzado el tope diario de gasto ({Config.TOPE_DIARIO_USD} USD; "
                f"gastado hoy {self.buzon.gasto_hoy():.2f}). Las órdenes quedan en cola hasta mañana "
                "o hasta que subas TOPE_DIARIO_USD y reinicies."])
        except Exception:
            logger.exception("No se pudo avisar del tope diario")

    async def despachar(self):
        """Cada segundo, arranca un trabajador por cada destino con mensajes pendientes."""
        n = self.buzon.reiniciar_en_curso()
        if n:
            logger.info(f"{n} mensajes que quedaron a medias vuelven a la cola")
        vigilancia: asyncio.Task | None = None
        while True:
            try:
                # Canal: cada 15 s, en una tarea aparte (una entrega por el relé tarda segundos).
                if (vigilancia is None or vigilancia.done()) and time.time() - self._ultima_vigilancia >= 15:
                    self._ultima_vigilancia = time.time()
                    vigilancia = asyncio.create_task(self._vigilar_seguro())
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

    async def _vigilar_seguro(self):
        try:
            await self.vigilar_canal()
        except Exception:
            logger.exception("Error vigilando el canal del jefe")

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

        inicio = time.time()
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
            await self.al_jefe([f"El subjefe ha fallado: {e}"], urgencias=self._urgencias_desde(inicio))
            return
        finally:
            escribiendo.cancel()
            self.recargar_empresas()

        reenviados = temas.tras_turno(self.buzon, filas, tema_id, tema_nuevo)
        if reenviados:
            logger.info(f"[subjefe] asunto nuevo: {reenviados} mensajes del jefe pasan a la sesión nueva")
        partes = [respuesta] if respuesta and respuesta.strip() != SILENCIO else []
        # También sin respuesta: puede quedar lo no visto o una alerta que copiar al PC.
        await self.al_jefe(partes, modo_voz=modo_voz, urgencias=self._urgencias_desde(inicio))

    def _urgencias_desde(self, inicio: float) -> list[str]:
        """Alertas que el subjefe hizo sonar en el móvil (ntfy) durante el turno."""
        return [f"[URGENTE] {a['titulo'] or 'Aviso'}: {a['mensaje']}"
                for a in self.buzon.alertas_enviadas_desde(inicio) if a["mensaje"]]

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
            if self.canal_activo() == "pc":  # el jefe está en el PC: nada en Telegram
                await asyncio.sleep(4.5)
                continue
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
    puente.jefe_por_telegram()
    puente.buzon.enviar("jefe", "subjefe", texto, modo_voz=modo_voz, canal="movil")
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
            "(no borra su carpeta ni, si es externa, su conversación)\n"
            "/pc, /movil: por dónde te llega lo del subjefe; /canal: cuál está activo"
        )


async def _cmd_canal(update: Update, canal: str):
    """/movil, /pc y /canal desde Telegram. Escribir aquí cuenta como haber visto lo enviado aquí."""
    if not await _autorizado(update):
        return
    if not puente.canal_pc_configurado():
        await update.effective_message.reply_text("El canal PC está desactivado (JEFE_PC_TITULO vacío): "
                                                  "todo llega por Telegram.")
        return
    ahora = time.time()
    puente.buzon.anotar_jefe("movil", ahora)
    if canal and puente.buzon.cambiar_canal(canal, "comando", ahora):
        logger.info(f"[canal] pasa a {canal} por comando desde Telegram")
    await update.effective_message.reply_text(herramientas.describir_canal(puente.buzon.estado_canal()))


async def cmd_movil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _cmd_canal(update, "movil")


async def cmd_pc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _cmd_canal(update, "pc")


async def cmd_canal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _cmd_canal(update, "")


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
    if puente.canal_pc_configurado():
        lineas.append(herramientas.describir_canal(puente.buzon.estado_canal()))
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
    app.add_handler(CommandHandler("movil", cmd_movil))
    app.add_handler(CommandHandler("pc", cmd_pc))
    app.add_handler(CommandHandler("canal", cmd_canal))
    # Solo mensajes nuevos: editar un mensaje no reenvía la orden (M7).
    solo_nuevos = filters.UpdateType.MESSAGE
    app.add_handler(MessageHandler(solo_nuevos & (filters.VOICE | filters.AUDIO), handle_voice))
    app.add_handler(MessageHandler(solo_nuevos & filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Puente activo. Esperando al jefe...")
    app.run_polling()


if __name__ == "__main__":
    main()
