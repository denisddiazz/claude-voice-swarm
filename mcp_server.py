"""Servidor MCP del buzón. Cada sesión de Claude Code lo lanza con su rol:

- ROL=subjefe -> encargar, crear_sesion, listar_sesiones, estado_empresas, alerta_urgente, cerrar_tema
- ROL=empresa -> informar_al_subjefe
- ROL=externa -> informar_al_subjefe para sesiones abiertas por el usuario (se dan de alta solas)
- ROL=jefe_pc -> hablar_al_subjefe y cambiar_canal, solo en la sesión de JEFE_PC_DIR (el jefe en el PC)

Nadie salvo el subjefe habla con el jefe, y solo a través del puente. Ningún rol
puede escribir en el buzón con origen 'jefe'. El rol, el nombre y el contador de
encargos encadenados (SALTOS) vienen del JSON de --mcp-config que escribe el puente.
PERMITIR_CREAR=1 solo lo pone el puente en un turno con un mensaje del jefe con firma válida.
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
    from empresas import ErrorEmpresas, cargar_todas
    from notificaciones import enviar_alerta_ntfy

    MAX_REBOTES = int(os.getenv("MAX_REBOTES", str(Config.MAX_REBOTES)))
    PERMITIR_CREAR = os.getenv("PERMITIR_CREAR") == "1"

    def _empresas():
        """Se relee en cada llamada: incluye las sesiones creadas en este mismo turno."""
        try:
            return cargar_todas(buzon), None
        except ErrorEmpresas as e:
            return None, str(e)

    @mcp.tool()
    async def encargar(empresa: str, orden: str) -> str:
        """Da una orden a una sesión. Su informe te llegará en un mensaje posterior con origen empresa:nombre."""
        empresas, error = _empresas()
        return herramientas.encargar(buzon, empresas, error, empresa, orden, SALTOS, MAX_REBOTES)

    @mcp.tool()
    async def crear_sesion(nombre: str, carpeta: str, descripcion: str, modelo: str | None = None) -> str:
        """Crea una sesión nueva de Claude Code en `carpeta` (la crea si no existe) y la deja lista para
        `encargar`. Solo si el jefe lo ha pedido expresamente en este turno. `nombre`: a-z, 0-9, _ y -."""
        empresas, _ = _empresas()
        return herramientas.crear_sesion(buzon, empresas, PERMITIR_CREAR, nombre, carpeta, descripcion, modelo)

    @mcp.tool()
    async def listar_sesiones() -> str:
        """Lista las sesiones disponibles con su descripción, carpeta y modelo."""
        empresas, error = _empresas()
        return herramientas.listar_sesiones(empresas, error)

    @mcp.tool()
    async def alerta_urgente(mensaje: str, titulo: str = "Aviso del subjefe") -> str:
        """Hace sonar el móvil del jefe con una alerta de máxima prioridad. Solo para lo grave o urgente."""
        if not buzon.puede_alertar(Config.ALERTA_INTERVALO_MIN * 60):
            buzon.registrar_alerta("subjefe", enviada=False)
            return (f"No enviada: ya se envió una alerta hace menos de {Config.ALERTA_INTERVALO_MIN} minutos. "
                    "Cuéntaselo al jefe en tu respuesta.")
        ok = await enviar_alerta_ntfy(mensaje, titulo=titulo)
        # El puente la copia además al PC si es el canal activo (con la respuesta de este turno).
        buzon.registrar_alerta("subjefe", enviada=ok, mensaje=mensaje, titulo=titulo)
        return "Alerta enviada al móvil del jefe." if ok else "No se pudo enviar la alerta."

    @mcp.tool()
    async def cerrar_tema(resumen: str, pendientes: str = "", nuevo_asunto: bool = False) -> str:
        """Da por terminado el tema actual. El próximo turno empieza una conversación nueva que solo
        recibe tu CLAUDE.md, este resumen y los mensajes nuevos. `resumen`: 1 a 3 líneas de lo hecho y
        decidido. `pendientes`: lo que queda abierto, incluidos los pendientes heredados no resueltos.
        `nuevo_asunto`: true si cierras porque el jefe ha cambiado de asunto; sus mensajes de este
        turno se te entregarán otra vez en la sesión nueva, así que no los atiendas ahora."""
        return herramientas.cerrar_tema(buzon, resumen, pendientes, nuevo_asunto)

    @mcp.tool()
    async def estado_empresas() -> str:
        """Muestra qué empresas están trabajando y cuántas órdenes tienen en cola."""
        empresas, error = _empresas()
        return herramientas.estado_empresas(buzon, empresas, error)

elif ROL == "empresa":
    if not herramientas.nombre_empresa_valido(NOMBRE):
        raise SystemExit(f"Nombre de empresa no válido para el servidor MCP: {NOMBRE!r}")

    @mcp.tool()
    async def informar_al_subjefe(mensaje: str, urgente: bool = False) -> str:
        """Envía un aviso al subjefe sin terminar tu trabajo. Usa urgente=true solo si es grave."""
        return herramientas.informar_al_subjefe(buzon, NOMBRE, mensaje, urgente, SALTOS)

elif ROL == "externa":
    # Registrado a nivel de usuario (registrar-mcp-usuario.ps1): lo cargan todas las sesiones
    # del usuario. En las que lanza el propio puente no ofrece nada: ya tienen mcp__buzon.
    # Tampoco en la ventana del jefe en el PC (JEFE_PC_DIR): no es una sesión de trabajo, no se da de alta.
    if os.getenv("CLAUDE_VOICE_SWARM_PUENTE") != "1" and not herramientas.es_carpeta_jefe_pc(
            os.getenv("CLAUDE_PROJECT_DIR") or os.getcwd()):
        import externas
        from empresas import ErrorEmpresas, cargar_todas

        @mcp.tool()
        async def informar_al_subjefe(mensaje: str, urgente: bool = False, nombre: str | None = None) -> str:
            """Envía un informe al subjefe, que se lo resume al jefe. La primera vez da de alta esta
            sesión (por defecto con el nombre de su carpeta; `nombre`: a-z, 0-9, _ y -) para que el
            subjefe pueda encargarle trabajo. Usa urgente=true solo si es grave."""
            cwd = os.getenv("CLAUDE_PROJECT_DIR") or os.getcwd()
            sid, metodo = externas.identificar_sesion(cwd, os.environ, mensaje)
            try:
                empresas, error = cargar_todas(buzon), None
            except ErrorEmpresas as e:
                empresas, error = None, str(e)
            return herramientas.informar_externo(buzon, empresas, error, cwd, sid, metodo, mensaje,
                                                 urgente, nombre)

elif ROL == "jefe_pc":
    # Lo carga C:\Cositas\Jefe\.mcp.json (ventana del jefe en el PC). Lo que escribe vale como del
    # jefe solo si el proceso corre en JEFE_PC_DIR; el puente lo adopta y lo firma (buzon.tomar_pendientes).
    # Límite: cualquier proceso local del usuario podría imitarlo, como con el resto del buzón.
    def _cwd():
        return os.getenv("CLAUDE_PROJECT_DIR") or os.getcwd()

    @mcp.tool()
    async def hablar_al_subjefe(texto: str) -> str:
        """Pasa al subjefe, literal, lo que ha escrito el jefe. La respuesta llegará a esta sesión
        como mensaje de otra sesión. '/movil', '/pc' y '/canal' cambian o consultan el canal."""
        return herramientas.hablar_al_subjefe(buzon, _cwd(), texto)

    @mcp.tool()
    async def cambiar_canal(canal: str = "") -> str:
        """Canal por el que el subjefe entrega al jefe: 'movil' (Telegram) o 'pc' (esta sesión).
        Vacío: solo dice cuál está activo."""
        return herramientas.cambiar_canal(buzon, _cwd(), canal)

else:
    raise SystemExit(f"ROL desconocido: {ROL!r}")


if __name__ == "__main__":
    mcp.run()
