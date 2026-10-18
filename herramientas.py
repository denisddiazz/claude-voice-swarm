"""Lógica de las herramientas del buzón, sin dependencia del paquete mcp (se prueba aparte).

El origen de lo que se escribe lo fija el rol del proceso, que viene de la configuración
que escribe el puente; nunca un argumento que controle el modelo.
"""
import os
import time
from datetime import datetime
from pathlib import Path

from buzon import Buzon
from config import Config
from empresas import NOMBRE_VALIDO, RESERVADOS, Empresa, ErrorEmpresas, resolver_directorio, validar_nombre
from externas import nombre_por_defecto


def nombre_empresa_valido(nombre: str) -> bool:
    return bool(NOMBRE_VALIDO.match(nombre or "")) and nombre not in RESERVADOS


# --- canal PC: la sesión de JEFE_PC_DIR es la ventana del jefe en el PC ---
def es_carpeta_jefe_pc(cwd) -> bool:
    try:
        return os.path.normcase(str(Path(str(cwd)).resolve())) == os.path.normcase(str(Config.JEFE_PC_DIR))
    except (OSError, ValueError):
        return False


NOMBRES_CANAL = {"movil": "móvil (Telegram)", "pc": "PC (ventana del jefe)"}
_MOTIVOS_CANAL = {"inicio": "por defecto", "jefe": "el jefe escribió por Telegram", "jefe_pc": "el jefe escribió en el PC",
                  "comando": "elegido por el jefe", "inactividad": "inactividad en el PC",
                  "fallo_rele": "no se pudo entregar en el PC"}
COMANDOS_CANAL = {"/movil": "movil", "/móvil": "movil", "/pc": "pc", "/canal": ""}


def describir_canal(estado) -> str:
    hora = datetime.fromtimestamp(estado["cambiado"]).strftime("%d/%m %H:%M")
    motivo = _MOTIVOS_CANAL.get(estado["motivo"], estado["motivo"])
    texto = f"Canal activo: {NOMBRES_CANAL.get(estado['activo'], estado['activo'])}, desde {hora} ({motivo})."
    if estado["activo"] == "pc" and Config.PC_INACTIVIDAD_MIN > 0:
        texto += f" Pasa al móvil tras {Config.PC_INACTIVIDAD_MIN:g} min sin actividad en el PC."
    return texto


def cambiar_canal(buzon: Buzon, cwd, canal: str) -> str:
    """Herramienta del rol jefe_pc: fija ('movil' o 'pc') o consulta ('') el canal."""
    if not es_carpeta_jefe_pc(cwd):
        return f"No permitido: solo la sesión de {Config.JEFE_PC_DIR} puede cambiar el canal."
    canal = (canal or "").strip().lower().lstrip("/").replace("ó", "o")
    if canal not in ("", "movil", "pc"):
        return "Canal desconocido: usa 'movil', 'pc' o vacío para consultarlo."
    if canal:
        ahora = time.time()
        buzon.anotar_jefe("pc", ahora)  # lo entregado aquí hasta ahora, el jefe lo tiene delante
        buzon.cambiar_canal(canal, "comando", ahora)
    return describir_canal(buzon.estado_canal())


def hablar_al_subjefe(buzon: Buzon, cwd, texto: str) -> str:
    """Herramienta del rol jefe_pc: deja en el buzón lo que el jefe ha escrito en el PC.

    Se inserta con origen 'jefe_pc' (este proceso no tiene la clave del puente); el puente lo firma
    como del jefe al tomarlo. El canal pasa a PC en el acto."""
    if not es_carpeta_jefe_pc(cwd):
        return f"No entregado: esta herramienta solo vale en la sesión de {Config.JEFE_PC_DIR}."
    texto = (texto or "").strip()
    if not texto:
        return "No entregado: el texto está vacío."
    if texto.lower() in COMANDOS_CANAL:
        return cambiar_canal(buzon, cwd, COMANDOS_CANAL[texto.lower()])
    ahora = time.time()
    buzon.cambiar_canal("pc", "jefe_pc", ahora)
    buzon.anotar_jefe("pc", ahora)
    buzon.enviar("jefe_pc", "subjefe", texto, modo_voz=False, canal="pc")
    return "Entregado al subjefe. Su respuesta llegará a esta sesión."


def encargar(buzon: Buzon, empresas: dict[str, Empresa] | None, error_carga: str | None,
             empresa: str, orden: str, saltos: int, max_rebotes: int) -> str:
    """max_rebotes <= 0: sin límite de encargos encadenados."""
    if empresas is None:
        return f"No puedo encargar nada: {error_carga or 'empresas.json no cargado'}"
    if empresa not in empresas:
        return f"No existe la sesión '{empresa}'. Disponibles: {', '.join(empresas) or '(ninguna)'}"
    nuevo = saltos + 1
    if max_rebotes > 0 and nuevo > max_rebotes:
        return (f"Límite de encargos encadenados alcanzado ({max_rebotes}) sin un mensaje del jefe. "
                "No se ha entregado la orden: explica al jefe la situación y pide confirmación.")
    buzon.enviar("subjefe", empresa, orden, saltos=nuevo)
    return f"Orden entregada a {empresa}. Su informe llegará más tarde."


RESUMEN_TEMA_MAX = 500
PENDIENTES_TEMA_MAX = 500


def cerrar_tema(buzon: Buzon, resumen: str, pendientes: str = "", nuevo_asunto: bool = False) -> str:
    """El subjefe da por terminado el tema: el puente empezará una conversación nueva en el próximo
    turno y le pasará este resumen. Los textos se recortan para que el estado no crezca."""
    resumen = " ".join((resumen or "").split())[:RESUMEN_TEMA_MAX]
    pendientes = " ".join((pendientes or "").split())[:PENDIENTES_TEMA_MAX]
    if not resumen:
        return "No cerrado: falta el resumen (de 1 a 3 líneas)."
    if buzon.cerrar_tema(resumen, pendientes, "subjefe", nuevo_asunto) is None:
        return "No hay ningún tema abierto."
    if nuevo_asunto:
        return ("Tema cerrado. No atiendas ahora el asunto nuevo: el puente te volverá a entregar los "
                "mensajes del jefe de este turno en una sesión nueva, con este resumen. Responde [SILENCIO].")
    return "Tema cerrado. El próximo turno empezará una sesión nueva con este resumen."


def crear_sesion(buzon: Buzon, empresas: dict[str, Empresa] | None, permitido: bool,
                 nombre: str, carpeta: str, descripcion: str, modelo: str | None = None,
                 protegidas: list[Path] | None = None) -> str:
    """Registra una sesión nueva. `permitido` lo decide el puente: solo es True en un turno
    del subjefe que contiene un mensaje del jefe con firma válida."""
    if not permitido:
        return ("No creada: solo puedo crear sesiones cuando el jefe lo pide en este mismo turno. "
                "Pregúntale si quiere crearla.")
    if empresas is None:
        return "No creada: no se pudo cargar la lista de sesiones."
    try:
        validar_nombre(nombre)
        if nombre in empresas:
            return f"No creada: ya existe una sesión llamada '{nombre}' ({empresas[nombre].directorio})."
        d = resolver_directorio(nombre, carpeta, protegidas)
        if modelo is not None and not isinstance(modelo, str):
            raise ErrorEmpresas("el modelo debe ser texto")
        d.mkdir(parents=True, exist_ok=True)
    except (ErrorEmpresas, OSError) as e:
        return f"No creada: {e}"
    buzon.registrar_sesion(nombre, str(d), descripcion or "", modelo or None)
    return f"Sesión '{nombre}' creada en {d}. Ya puedes encargarle trabajo."


def listar_sesiones(empresas: dict[str, Empresa] | None, error_carga: str | None) -> str:
    if empresas is None:
        return f"No hay sesiones cargadas: {error_carga}"
    if not empresas:
        return "No hay sesiones. El jefe puede pedirte que crees una."
    return "\n".join(
        f"{e.nombre}: {e.descripcion or '(sin descripción)'} | carpeta {e.directorio}"
        f"{' | modelo ' + e.modelo if e.modelo else ''} | origen {e.origen}"
        f"{' | conversación ' + e.session_id[:8] if e.session_id else ''}"
        for e in empresas.values())


def informar_al_subjefe(buzon: Buzon, nombre: str, mensaje: str, urgente: bool, saltos: int) -> str:
    if not nombre_empresa_valido(nombre):
        return "Rol de empresa no válido: aviso no entregado."
    buzon.enviar(nombre, "subjefe", mensaje, urgente=bool(urgente), saltos=saltos)
    return "Aviso entregado al subjefe."


_AVISO_METODO = {
    "transcripcion": "",
    "reciente": (" Aviso: he identificado esta conversación por ser la más reciente de la carpeta; si hay otra "
                 "sesión abierta en esta misma carpeta, puede haberse dado de alta la otra."),
}


def informar_externo(buzon: Buzon, empresas: dict[str, Empresa] | None, error_carga: str | None,
                     cwd, session_id: str | None, metodo: str, mensaje: str, urgente: bool = False,
                     nombre: str | None = None, protegidas: list[Path] | None = None) -> str:
    """informar_al_subjefe de una sesión externa (abierta por el usuario, no por el puente).

    La sesión se identifica por su carpeta (cwd) y su conversación (session_id). El primer
    informe la da de alta con origen 'externa' para que `encargar` reanude esa conversación.
    Si la carpeta ya tiene una sesión externa y llama otra conversación, pasa a ser esta."""
    if es_carpeta_jefe_pc(cwd):
        return ("Aviso no entregado: esta es la ventana del jefe en el PC, no una sesión de trabajo. "
                "Usa hablar_al_subjefe.")
    if empresas is None:
        return f"Aviso no entregado: no se pudo cargar la lista de sesiones ({error_carga})."
    try:
        d = resolver_directorio("sesion", str(cwd), protegidas)
    except ErrorEmpresas as e:
        return f"Aviso no entregado: esta carpeta no puede ser una sesión del subjefe ({e})."

    destino, alta = None, ""
    if session_id:
        destino = next((e.nombre for e in empresas.values() if e.session_id == session_id), None)
        if destino is None:
            propia = buzon.nombre_por_session_id(session_id)  # conversación lanzada por el propio puente
            if propia == "subjefe":
                return "Aviso no entregado: el subjefe no se informa a sí mismo."
            destino = propia if propia in empresas else None
    if destino is None:
        fila = buzon.externa_por_directorio(str(d))
        if fila is not None:
            destino = fila["nombre"]
            if session_id and fila["session_id"] != session_id:
                buzon.actualizar_session_id_registrada(destino, session_id)
                alta = f" Esta conversación pasa a ser la sesión '{destino}'."
    if destino is None:
        if not session_id:
            buzon.enviar("sesion-externa", "subjefe", mensaje, urgente=bool(urgente))
            return ("Aviso entregado, pero no he podido identificar esta conversación: no queda dada de alta "
                    "y el subjefe no podrá encargarle trabajo.")
        ocupados = set(empresas) | {f["nombre"] for f in buzon.sesiones_registradas()}
        if nombre:
            try:
                validar_nombre(nombre)
                if nombre in ocupados:
                    raise ErrorEmpresas(f"ya existe una sesión llamada '{nombre}'")
            except ErrorEmpresas as e:
                return f"Aviso no entregado: {e}. Prueba con otro nombre o sin nombre."
        destino = nombre or nombre_por_defecto(d, ocupados)
        buzon.registrar_sesion(destino, str(d), f"Sesión principal de {d.name} (abierta por el jefe)", None,
                               origen="externa", session_id=session_id)
        alta = f" Esta sesión queda dada de alta como '{destino}'; el subjefe podrá encargarle trabajo."
    buzon.enviar(destino, "subjefe", mensaje, urgente=bool(urgente))
    return f"Aviso entregado al subjefe.{alta}{_AVISO_METODO.get(metodo, '') if alta else ''}"


def estado_empresas(buzon: Buzon, empresas: dict[str, Empresa] | None, error_carga: str | None) -> str:
    if empresas is None:
        return f"No hay sesiones cargadas: {error_carga}"
    estado = buzon.resumen_estado()
    lineas = []
    for nombre in empresas:
        e = estado.get(nombre, {})
        trabajando = "trabajando" if e.get("en_curso") else "libre"
        lineas.append(f"{nombre}: {trabajando}, {e.get('pendiente', 0)} órdenes en cola")
    return "\n".join(lineas) or "No hay sesiones configuradas."
