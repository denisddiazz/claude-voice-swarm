"""Carga empresas.json: qué empresas existen y cómo se lanza cada sesión."""
import json
from dataclasses import dataclass, field
from pathlib import Path

from config import Config

RESERVADOS = {"jefe", "subjefe"}


@dataclass
class Empresa:
    nombre: str
    descripcion: str
    directorio: Path
    modelo: str = "opus"
    permission_mode: str = "acceptEdits"
    allowed_tools: list[str] = field(default_factory=list)
    max_budget_usd: float | None = None


def cargar_empresas(ruta: Path = None) -> dict[str, Empresa]:
    ruta = Path(ruta or Config.EMPRESAS_FILE)
    if not ruta.exists():
        raise SystemExit(f"No existe {ruta}. Copia empresas.json.example a empresas.json y edítalo.")
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    empresas = {}
    for nombre, conf in datos.items():
        if nombre in RESERVADOS:
            raise SystemExit(f"'{nombre}' es un nombre reservado, usa otro en {ruta}")
        empresas[nombre] = Empresa(
            nombre=nombre,
            descripcion=conf.get("descripcion", ""),
            directorio=Path(conf.get("directorio", "~")).expanduser().resolve(),
            modelo=conf.get("modelo", "opus"),
            permission_mode=conf.get("permission_mode", "acceptEdits"),
            allowed_tools=list(conf.get("allowed_tools", [])),
            max_budget_usd=conf.get("max_budget_usd"),
        )
    return empresas
