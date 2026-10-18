import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import sesiones  # noqa: E402
from buzon import Buzon  # noqa: E402
from config import Config  # noqa: E402

CLAUDE_FALSO = Path(__file__).resolve().parent / "claude_falso.py"


@pytest.fixture(autouse=True)
def datos_temporales(tmp_path, monkeypatch):
    """Nada de lo que escriben las pruebas cae en datos/ del repositorio."""
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path / "datos")
    monkeypatch.setattr(Config, "BUZON_DB", tmp_path / "datos" / "buzon.sqlite3")
    monkeypatch.setattr(Config, "SUBJEFE_DIR", tmp_path / "subjefe")
    monkeypatch.setattr(Config, "CLAUDE_PROJECTS_DIR", tmp_path / "claude_projects")
    # Valores por defecto, no los del .env real ni del entorno de quien ejecuta las pruebas.
    for nombre, valor in {"SUBJEFE_MODEL": "haiku", "SUBJEFE_EFFORT": "medium", "TEMA_INACTIVIDAD_MIN": 120,
                          "TEMA_MAX_KB": 600, "TEMA_ESTADO_MAX_CHARS": 1500,
                          "SUBJEFE_PC_TITULO": "Subjefe", "PC_INACTIVIDAD_MIN": 15,
                          "PC_INACTIVIDAD_FUENTE": "entrada",
                          "RELE_PC_MODELO": "haiku", "RELE_PC_TIMEOUT": 60, "RELE_PC_MAX_BUDGET_USD": 0.05}.items():
        monkeypatch.setattr(Config, nombre, valor)


@pytest.fixture
def buzon(tmp_path):
    return Buzon(tmp_path / "buzon.sqlite3", clave=b"k" * 32)


class ClaudeFalso:
    def __init__(self, tmp_path: Path):
        self.control = tmp_path / "control.json"
        self.configurar()

    def configurar(self, modos=("ok",), resultado="hecho", coste=0.0, existentes=(), cierre=None,
                   rele="ok", rele_coste=0.002):
        self.control.write_text(json.dumps({"modos": list(modos), "resultado": resultado, "coste": coste,
                                            "existentes": list(existentes), "cierre": cierre or {},
                                            "rele": rele, "rele_coste": rele_coste}),
                                encoding="utf-8")

    def llamadas(self) -> list[dict]:
        log = Path(str(self.control) + ".log")
        if not log.exists():
            return []
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.fixture
def claude_falso(tmp_path, monkeypatch):
    falso = ClaudeFalso(tmp_path)
    monkeypatch.setattr(sesiones, "_comando_claude", lambda: [sys.executable, str(CLAUDE_FALSO), str(falso.control)])
    return falso


def correr(coro):
    return asyncio.run(coro)
