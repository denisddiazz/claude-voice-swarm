"""A1: las rutas de configuración se resuelven contra la raíz del proyecto, no contra el cwd."""
import json
import os
import subprocess
import sys

import pytest

import herramientas
from config import BASE_DIR, ruta_proyecto
from conftest import REPO
from empresas import ErrorEmpresas, cargar_empresas


def test_ruta_relativa_se_resuelve_contra_el_proyecto():
    assert ruta_proyecto("empresas.json", BASE_DIR / "x") == (BASE_DIR / "empresas.json").resolve()
    assert ruta_proyecto(None, BASE_DIR / "datos") == (BASE_DIR / "datos").resolve()


def test_config_desde_otro_directorio_de_trabajo(tmp_path):
    """Como el servidor MCP, que Claude Code lanza desde la carpeta de la sesión."""
    env = dict(os.environ, EMPRESAS_FILE="empresas.json", DATA_DIR="datos", PYTHONPATH=str(REPO))
    codigo = "import json; from config import Config; print(json.dumps([str(Config.EMPRESAS_FILE), str(Config.DATA_DIR)]))"
    out = subprocess.run([sys.executable, "-c", codigo], cwd=tmp_path, env=env,
                         capture_output=True, text=True, check=True).stdout
    empresas_file, data_dir = json.loads(out)
    assert empresas_file == str((REPO / "empresas.json").resolve())
    assert data_dir == str((REPO / "datos").resolve())


def test_ruta_absoluta_se_respeta(tmp_path):
    assert ruta_proyecto(str(tmp_path / "e.json"), BASE_DIR) == (tmp_path / "e.json").resolve()


def test_empresas_inexistente_lanza_error_normal_no_systemexit(tmp_path):
    with pytest.raises(ErrorEmpresas):
        cargar_empresas(tmp_path / "no_existe.json")
    assert issubclass(ErrorEmpresas, Exception)


def test_encargar_sin_empresas_devuelve_texto(buzon):
    r = herramientas.encargar(buzon, None, "No existe empresas.json", "codigo", "haz algo", 0, 3)
    assert "No puedo encargar" in r
    assert buzon.destinos_pendientes() == []
