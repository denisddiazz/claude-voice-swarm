"""Lógica de las herramientas del buzón, sin dependencia del paquete mcp (se prueba aparte).

El origen de lo que se escribe lo fija el rol del proceso, que viene de la configuración
que escribe el puente; nunca un argumento que controle el modelo.
"""
from pathlib import Path

from buzon import Buzon
from empresas import NOMBRE_VALIDO, RESERVADOS, Empresa, ErrorEmpresas, resolver_directorio, validar_nombre


def nombre_empresa_valido(nombre: str) -> bool:
    return bool(NOMBRE_VALIDO.match(nombre or "")) and nombre not in RESERVADOS


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
        f"{' | modelo ' + e.modelo if e.modelo else ''} | {e.origen}"
        for e in empresas.values())


def informar_al_subjefe(buzon: Buzon, nombre: str, mensaje: str, urgente: bool, saltos: int) -> str:
    if not nombre_empresa_valido(nombre):
        return "Rol de empresa no válido: aviso no entregado."
    buzon.enviar(nombre, "subjefe", mensaje, urgente=bool(urgente), saltos=saltos)
    return "Aviso entregado al subjefe."


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
