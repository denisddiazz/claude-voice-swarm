"""Carga y valida empresas.json: qué empresas existen y cómo se lanza cada sesión."""
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from config import BASE_DIR, ENV_FILE, Config

logger = logging.getLogger(__name__)

RESERVADOS = {"jefe", "subjefe", "sistema"}
NOMBRE_VALIDO = re.compile(r"^[a-z0-9_-]{1,40}$")
MODOS_PERMISO = {"default", "acceptEdits", "dontAsk", "plan", "auto", "bypassPermissions"}
# Reglas de prefijo que equivalen a ejecutar código arbitrario (intérpretes, gestores de
# paquetes, git con config/hook/alias). Se avisa al cargar; ver "Modelo de permisos" en README.
_PELIGROSAS = re.compile(
    r"^Bash(\((\*|(python3?|py|node|npm|npx|pnpm|yarn|pip3?|uv|git|bash|sh|zsh|pwsh|powershell|"
    r"perl|ruby|php|make|cargo|go|docker|sudo|env|xargs|find|awk|sed)\b.*)\))?$"
)


class ErrorEmpresas(ValueError):
    """empresas.json no existe o no es válido."""


@dataclass
class Empresa:
    nombre: str
    descripcion: str
    directorio: Path
    max_budget_usd: float
    modelo: str = "opus"
    permission_mode: str = "acceptEdits"
    allowed_tools: list[str] = field(default_factory=list)
    permitir_bypass: bool = False
    restringida: bool = False  # --restricted: sin Bash/WebFetch, ficheros solo en su carpeta


def _dentro(a: Path, b: Path) -> bool:
    """True si a es b o está dentro de b."""
    return a == b or b in a.parents


def validar_directorio(nombre: str, directorio: Path, home: Path | None = None,
                       protegidas: list[Path] | None = None) -> None:
    """Rechaza carpetas de trabajo que expongan el home, la raíz o el propio puente."""
    home = (home or Path.home()).resolve()
    if protegidas is None:
        protegidas = [BASE_DIR, Config.DATA_DIR, Config.EMPRESAS_FILE, ENV_FILE]
    protegidas = [Path(p).resolve() for p in protegidas]
    d = directorio
    if d.parent == d:
        raise ErrorEmpresas(f"{nombre}: el directorio no puede ser la raíz del sistema ({d})")
    if _dentro(home, d):
        raise ErrorEmpresas(f"{nombre}: el directorio {d} es el home o lo contiene; usa una carpeta dedicada")
    for p in protegidas:
        if _dentro(p, d):
            raise ErrorEmpresas(f"{nombre}: el directorio {d} contiene {p} (el puente, sus datos o .env)")
        if _dentro(d, p):
            raise ErrorEmpresas(f"{nombre}: el directorio {d} está dentro de {p}")


def reglas_peligrosas(allowed_tools: list[str]) -> list[str]:
    return [r for r in allowed_tools if _PELIGROSAS.match(r.strip())]


def cargar_empresas(ruta: Path = None, home: Path | None = None, protegidas: list[Path] | None = None) -> dict[str, Empresa]:
    ruta = Path(ruta or Config.EMPRESAS_FILE)
    if not ruta.exists():
        raise ErrorEmpresas(f"No existe {ruta}. Copia empresas.json.example a empresas.json y edítalo.")
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ErrorEmpresas(f"{ruta} no es JSON válido: {e}") from e
    if not isinstance(datos, dict):
        raise ErrorEmpresas(f"{ruta} debe ser un objeto {{nombre: configuración}}")

    empresas = {}
    for nombre, conf in datos.items():
        if nombre in RESERVADOS:
            raise ErrorEmpresas(f"'{nombre}' es un nombre reservado, usa otro en {ruta}")
        if not NOMBRE_VALIDO.match(nombre):
            raise ErrorEmpresas(f"'{nombre}': el nombre solo admite a-z, 0-9, '_' y '-'")
        if not isinstance(conf, dict):
            raise ErrorEmpresas(f"{nombre}: la configuración debe ser un objeto")

        if not conf.get("directorio"):
            raise ErrorEmpresas(f"{nombre}: falta 'directorio' (obligatorio, carpeta dedicada)")
        directorio = Path(str(conf["directorio"])).expanduser()
        if not directorio.is_absolute():
            raise ErrorEmpresas(f"{nombre}: 'directorio' debe ser absoluto o empezar por ~")
        directorio = directorio.resolve()
        validar_directorio(nombre, directorio, home=home, protegidas=protegidas)

        presupuesto = conf.get("max_budget_usd")
        if isinstance(presupuesto, bool) or not isinstance(presupuesto, (int, float)) or presupuesto <= 0:
            raise ErrorEmpresas(f"{nombre}: 'max_budget_usd' es obligatorio y debe ser un número > 0")

        modo = conf.get("permission_mode", "acceptEdits")
        if modo not in MODOS_PERMISO:
            raise ErrorEmpresas(f"{nombre}: permission_mode '{modo}' no válido ({', '.join(sorted(MODOS_PERMISO))})")
        permitir_bypass = conf.get("permitir_bypass") is True
        if modo == "bypassPermissions" and conf.get("restringida") is True:
            raise ErrorEmpresas(f"{nombre}: 'restringida' no es compatible con bypassPermissions")
        if modo == "bypassPermissions" and not permitir_bypass:
            raise ErrorEmpresas(
                f"{nombre}: bypassPermissions exige \"permitir_bypass\": true en esa empresa (ver README)"
            )

        allowed = conf.get("allowed_tools", [])
        if not isinstance(allowed, list) or not all(isinstance(x, str) for x in allowed):
            raise ErrorEmpresas(f"{nombre}: 'allowed_tools' debe ser una lista de textos")
        for r in reglas_peligrosas(allowed):
            logger.warning(f"{nombre}: la regla {r!r} equivale a ejecutar código arbitrario (ver README)")

        empresas[nombre] = Empresa(
            nombre=nombre,
            descripcion=str(conf.get("descripcion", "")),
            directorio=directorio,
            max_budget_usd=float(presupuesto),
            modelo=str(conf.get("modelo", "opus")),
            permission_mode=modo,
            allowed_tools=list(allowed),
            permitir_bypass=permitir_bypass,
            restringida=conf.get("restringida") is True,
        )
    return empresas
