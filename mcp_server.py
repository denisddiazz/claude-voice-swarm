"""Servidor MCP del buzón. Cada sesión de Claude Code lo lanza con su rol:

- ROL=subjefe -> encargar, alerta_urgente, estado_empresas
- ROL=empresa -> informar_al_subjefe

Nadie salvo el subjefe habla con el jefe, y solo a través del puente. Ningún rol
puede escribir en el buzón con origen 'jefe'. El rol, el nombre y el contador de
encargos encadenados (SALTOS) vienen del JSON de --mcp-config que escribe el puente.
"""
import os

from mcp.server.mcpserver import MCPServer

import herramientas
from buzon import Buzon

ROL = os.getenv("ROL", "empresa")
NOMBRE = os.getenv("EMPRESA", "")
SALTOS = int(os.getenv("SALTOS", "0") or "0")
buzon = Buzon()
mcp = MCPServer("buzon")

if ROL == "subjefe":
    from config import Config
    from empresas import ErrorEmpresas, cargar_empresas
    from notificaciones import enviar_alerta_ntfy

    MAX_REBOTES = int(os.getenv("MAX_REBOTES", str(Config.MAX_REBOTES)))
    try:
        EMPRESAS, ERROR_CARGA = cargar_empresas(), None
    except ErrorEmpresas as e:
        EMPRESAS, ERROR_CARGA = None, str(e)

    @mcp.tool()
    async def encargar(empresa: str, orden: str) -> str:
        """Da una orden a una empresa. Su informe te llegará en un mensaje posterior con origen empresa:nombre."""
        return herramientas.encargar(buzon, EMPRESAS, ERROR_CARGA, empresa, orden, SALTOS, MAX_REBOTES)

    @mcp.tool()
    async def alerta_urgente(mensaje: str, titulo: str = "Aviso del subjefe") -> str:
        """Hace sonar el móvil del jefe con una alerta de máxima prioridad. Solo para lo grave o urgente."""
        if not buzon.puede_alertar(Config.ALERTA_INTERVALO_MIN * 60):
            buzon.registrar_alerta("subjefe", enviada=False)
            return (f"No enviada: ya se envió una alerta hace menos de {Config.ALERTA_INTERVALO_MIN} minutos. "
                    "Cuéntaselo al jefe en tu respuesta.")
        ok = await enviar_alerta_ntfy(mensaje, titulo=titulo)
        buzon.registrar_alerta("subjefe", enviada=ok)
        return "Alerta enviada al móvil del jefe." if ok else "No se pudo enviar la alerta."

    @mcp.tool()
    async def estado_empresas() -> str:
        """Muestra qué empresas están trabajando y cuántas órdenes tienen en cola."""
        return herramientas.estado_empresas(buzon, EMPRESAS, ERROR_CARGA)

elif ROL == "empresa":
    if not herramientas.nombre_empresa_valido(NOMBRE):
        raise SystemExit(f"Nombre de empresa no válido para el servidor MCP: {NOMBRE!r}")

    @mcp.tool()
    async def informar_al_subjefe(mensaje: str, urgente: bool = False) -> str:
        """Envía un aviso al subjefe sin terminar tu trabajo. Usa urgente=true solo si es grave."""
        return herramientas.informar_al_subjefe(buzon, NOMBRE, mensaje, urgente, SALTOS)

else:
    raise SystemExit(f"ROL desconocido: {ROL!r}")


if __name__ == "__main__":
    mcp.run()
