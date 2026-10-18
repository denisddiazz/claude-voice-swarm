"""Lógica de las herramientas del buzón, sin dependencia del paquete mcp (se prueba aparte).

El origen de lo que se escribe lo fija el rol del proceso, que viene de la configuración
que escribe el puente; nunca un argumento que controle el modelo.
"""
from buzon import Buzon
from empresas import NOMBRE_VALIDO, RESERVADOS, Empresa


def nombre_empresa_valido(nombre: str) -> bool:
    return bool(NOMBRE_VALIDO.match(nombre or "")) and nombre not in RESERVADOS


def encargar(buzon: Buzon, empresas: dict[str, Empresa] | None, error_carga: str | None,
             empresa: str, orden: str, saltos: int, max_rebotes: int) -> str:
    if empresas is None:
        return f"No puedo encargar nada: {error_carga or 'empresas.json no cargado'}"
    if empresa not in empresas:
        return f"No existe la empresa '{empresa}'. Disponibles: {', '.join(empresas) or '(ninguna)'}"
    nuevo = saltos + 1
    if nuevo > max_rebotes:
        return (f"Límite de encargos encadenados alcanzado ({max_rebotes}) sin un mensaje del jefe. "
                "No se ha entregado la orden: explica al jefe la situación y pide confirmación.")
    buzon.enviar("subjefe", empresa, orden, saltos=nuevo)
    return f"Orden entregada a {empresa}. Su informe llegará más tarde."


def informar_al_subjefe(buzon: Buzon, nombre: str, mensaje: str, urgente: bool, saltos: int) -> str:
    if not nombre_empresa_valido(nombre):
        return "Rol de empresa no válido: aviso no entregado."
    buzon.enviar(nombre, "subjefe", mensaje, urgente=bool(urgente), saltos=saltos)
    return "Aviso entregado al subjefe."


def estado_empresas(buzon: Buzon, empresas: dict[str, Empresa] | None, error_carga: str | None) -> str:
    if empresas is None:
        return f"No hay empresas cargadas: {error_carga}"
    estado = buzon.resumen_estado()
    lineas = []
    for nombre in empresas:
        e = estado.get(nombre, {})
        trabajando = "trabajando" if e.get("en_curso") else "libre"
        lineas.append(f"{nombre}: {trabajando}, {e.get('pendiente', 0)} órdenes en cola")
    return "\n".join(lineas) or "No hay empresas configuradas."
