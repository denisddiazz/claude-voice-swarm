"""Sesiones de abajo ("empresas"): las de empresas.json, las creadas con crear_sesion y las
externas (sesiones abiertas por el usuario que se dieron de alta al informar al subjefe).

Cada sesión es una sesión normal de Claude Code en su carpeta y usa su propia
configuración (settings de usuario, de proyecto y locales). El puente no impone
permisos: `modelo`, `permission_mode`, `allowed_tools` y `setting_sources` son
sustituciones opcionales por sesión. Con `session_id`, el puente reanuda esa
conversación ya existente en lugar de crear una propia.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from config import BASE_DIR, Config

RESERVADOS = {"jefe", "subjefe", "sistema", "jefe_pc"}  # jefe_pc: origen de lo que el jefe escribe en el PC
NOMBRE_VALIDO = re.compile(r"^[a-z0-9_-]{1,40}$")
MODOS_PERMISO = {"default", "acceptEdits", "dontAsk", "plan", "auto", "bypassPermissions"}
SESSION_ID_VALIDO = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class ErrorEmpresas(ValueError):
    """empresas.json no es válido, o una sesión nueva no se puede crear."""


@dataclass
class Empresa:
    nombre: str
    descripcion: str
    directorio: Path
    modelo: str | None = None              # None: el modelo por defecto de esa sesión
    permission_mode: str | None = None     # None: el de los settings de la carpeta o del usuario
    allowed_tools: list[str] | None = None
    setting_sources: str | None = None
    max_budget_usd: float | None = None    # None: EMPRESA_MAX_BUDGET_USD de .env (0 = sin tope)
    origen: str = "empresas.json"          # o "creada" (con crear_sesion) o "externa" (alta al informar)
    session_id: str | None = None          # conversación existente que se reanuda; None: la crea el puente


def _dentro(a: Path, b: Path) -> bool:
    """True si a es b o está dentro de b."""
    return a == b or b in a.parents


def carpetas_protegidas() -> list[Path]:
    """Ninguna sesión puede trabajar en la carpeta del puente, la de sus datos ni la del subjefe."""
    return [BASE_DIR.resolve(), Config.DATA_DIR.resolve(), Config.SUBJEFE_DIR.resolve()]


def resolver_directorio(nombre: str, valor, protegidas: list[Path] | None = None) -> Path:
    """La única regla de carpetas: no puede ser la del puente ni la de datos/, ni estar dentro."""
    if not valor:
        raise ErrorEmpresas(f"{nombre}: falta la carpeta de la sesión")
    d = Path(str(valor)).expanduser()
    if not d.is_absolute():
        raise ErrorEmpresas(f"{nombre}: la carpeta debe ser una ruta absoluta o empezar por ~")
    d = d.resolve()
    for p in (protegidas if protegidas is not None else carpetas_protegidas()):
        if _dentro(d, Path(p).resolve()):
            raise ErrorEmpresas(f"{nombre}: la carpeta {d} es la del puente o la de sus datos ({p})")
    return d


def validar_nombre(nombre: str):
    if nombre in RESERVADOS:
        raise ErrorEmpresas(f"'{nombre}' es un nombre reservado")
    if not NOMBRE_VALIDO.match(nombre or ""):
        raise ErrorEmpresas(f"'{nombre}': el nombre solo admite a-z, 0-9, '_' y '-' (máx. 40)")


def _opcional_str(nombre, conf, campo):
    v = conf.get(campo)
    if v is not None and not isinstance(v, str):
        raise ErrorEmpresas(f"{nombre}: '{campo}' debe ser texto")
    return v or None


def desde_conf(nombre: str, conf: dict, origen: str = "empresas.json",
               protegidas: list[Path] | None = None) -> Empresa:
    validar_nombre(nombre)
    if not isinstance(conf, dict):
        raise ErrorEmpresas(f"{nombre}: la configuración debe ser un objeto")
    directorio = resolver_directorio(nombre, conf.get("directorio"), protegidas)

    modo = _opcional_str(nombre, conf, "permission_mode")
    if modo is not None and modo not in MODOS_PERMISO:
        raise ErrorEmpresas(f"{nombre}: permission_mode '{modo}' no válido ({', '.join(sorted(MODOS_PERMISO))})")
    allowed = conf.get("allowed_tools")
    if allowed is not None and (not isinstance(allowed, list) or not all(isinstance(x, str) for x in allowed)):
        raise ErrorEmpresas(f"{nombre}: 'allowed_tools' debe ser una lista de textos")
    presupuesto = conf.get("max_budget_usd")
    if presupuesto is not None and (isinstance(presupuesto, bool) or not isinstance(presupuesto, (int, float))
                                    or presupuesto < 0):
        raise ErrorEmpresas(f"{nombre}: 'max_budget_usd' debe ser un número >= 0 (0 = sin tope)")
    session_id = _opcional_str(nombre, conf, "session_id")
    if session_id is not None and not SESSION_ID_VALIDO.match(session_id):
        raise ErrorEmpresas(f"{nombre}: 'session_id' debe ser el UUID de una conversación de Claude Code")

    return Empresa(
        nombre=nombre,
        descripcion=str(conf.get("descripcion", "")),
        directorio=directorio,
        modelo=_opcional_str(nombre, conf, "modelo"),
        permission_mode=modo,
        allowed_tools=list(allowed) if allowed is not None else None,
        setting_sources=_opcional_str(nombre, conf, "setting_sources"),
        max_budget_usd=float(presupuesto) if presupuesto is not None else None,
        origen=origen,
        session_id=session_id.lower() if session_id else None,
    )


def cargar_empresas(ruta: Path = None, protegidas: list[Path] | None = None) -> dict[str, Empresa]:
    """Lee empresas.json. Si no existe, no hay sesiones fijas (se pueden crear con crear_sesion)."""
    ruta = Path(ruta or Config.EMPRESAS_FILE)
    if not ruta.exists():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ErrorEmpresas(f"{ruta} no es JSON válido: {e}") from e
    if not isinstance(datos, dict):
        raise ErrorEmpresas(f"{ruta} debe ser un objeto {{nombre: configuración}}")
    return {n: desde_conf(n, c, protegidas=protegidas) for n, c in datos.items()}


def cargar_todas(buzon, ruta: Path = None, protegidas: list[Path] | None = None) -> dict[str, Empresa]:
    """empresas.json más las sesiones registradas en el buzón. empresas.json manda si se repite un nombre."""
    empresas = cargar_empresas(ruta, protegidas)
    for fila in buzon.sesiones_registradas():
        if fila["nombre"] in empresas:
            continue
        try:
            empresas[fila["nombre"]] = desde_conf(
                fila["nombre"],
                {"directorio": fila["directorio"], "descripcion": fila["descripcion"], "modelo": fila["modelo"],
                 "session_id": fila["session_id"]},
                origen=fila["origen"] or "creada", protegidas=protegidas)
        except ErrorEmpresas:
            continue  # una fila inválida no tumba al resto
    return empresas
