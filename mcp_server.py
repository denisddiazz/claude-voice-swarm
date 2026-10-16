"""Servidor MCP del buzón. Cada sesión de Claude Code lo lanza con su rol:

- ROL=subjefe -> encargar, alerta_urgente, estado_empresas
- ROL=empresa -> informar_al_subjefe

Nadie salvo el subjefe habla con el jefe, y solo a través del puente.
"""
import os

from mcp.server.mcpserver import MCPServer

from buzon import Buzon
from empresas import cargar_empresas
from notificaciones import enviar_alerta_ntfy

ROL = os.getenv("ROL", "empresa")
NOMBRE = os.getenv("EMPRESA", "desconocida")
buzon = Buzon()
mcp = MCPServer("buzon")

if ROL == "subjefe":

    @mcp.tool()
    async def encargar(empresa: str, orden: str) -> str:
        """Da una orden a una empresa. Su respuesta te llegará en un mensaje posterior etiquetado [EMPRESA nombre]."""
        empresas = cargar_empresas()
        if empresa not in empresas:
            return f"No existe la empresa '{empresa}'. Disponibles: {', '.join(empresas)}"
        buzon.enviar("subjefe", empresa, orden)
        return f"Orden entregada a {empresa}. Su informe llegará más tarde."

    @mcp.tool()
    async def alerta_urgente(mensaje: str, titulo: str = "Aviso del subjefe") -> str:
        """Hace sonar el móvil del jefe con una alerta de máxima prioridad. Solo para lo grave o urgente."""
        ok = await enviar_alerta_ntfy(mensaje, titulo=titulo)
        return "Alerta enviada al móvil del jefe." if ok else "No se pudo enviar la alerta."

    @mcp.tool()
    async def estado_empresas() -> str:
        """Muestra qué empresas están trabajando y cuántas órdenes tienen en cola."""
        estado = buzon.resumen_estado()
        lineas = []
        for nombre in cargar_empresas():
            e = estado.get(nombre, {})
            trabajando = "trabajando" if e.get("en_curso") else "libre"
            lineas.append(f"{nombre}: {trabajando}, {e.get('pendiente', 0)} órdenes en cola")
        return "\n".join(lineas) or "No hay empresas configuradas."

else:

    @mcp.tool()
    async def informar_al_subjefe(mensaje: str, urgente: bool = False) -> str:
        """Envía un aviso al subjefe sin terminar tu trabajo. Usa urgente=true solo si es grave."""
        buzon.enviar(NOMBRE, "subjefe", mensaje, urgente=urgente)
        return "Aviso entregado al subjefe."


if __name__ == "__main__":
    mcp.run()
