"""A2/A3: validación de empresas.json (carpetas, bypass, reglas peligrosas, presupuesto)."""
import json
import logging
from pathlib import Path

import pytest

from conftest import REPO
from empresas import ErrorEmpresas, cargar_empresas, reglas_peligrosas


def _cargar(tmp_path, conf, home=None, protegidas=None):
    ruta = tmp_path / "empresas.json"
    ruta.write_text(json.dumps(conf), encoding="utf-8")
    home = home or (tmp_path / "home")
    home.mkdir(exist_ok=True)
    protegidas = protegidas if protegidas is not None else [tmp_path / "repo", tmp_path / "repo" / "datos"]
    return cargar_empresas(ruta, home=home, protegidas=protegidas)


def _empresa(directorio, **extra):
    return {"x": {"descripcion": "d", "directorio": str(directorio), "max_budget_usd": 1, **extra}}


def test_carpeta_dedicada_valida(tmp_path):
    e = _cargar(tmp_path, _empresa(tmp_path / "home" / "empresas" / "x"))
    assert e["x"].max_budget_usd == 1


@pytest.mark.parametrize("rel", ["home", ".", "repo", "repo/datos", "repo/sub"])
def test_rechaza_home_ancestros_y_repo(tmp_path, rel):
    with pytest.raises(ErrorEmpresas):
        _cargar(tmp_path, _empresa(tmp_path / rel))


def test_rechaza_raiz(tmp_path):
    raiz = Path(tmp_path.anchor)
    with pytest.raises(ErrorEmpresas):
        _cargar(tmp_path, _empresa(raiz))


def test_directorio_obligatorio(tmp_path):
    with pytest.raises(ErrorEmpresas, match="directorio"):
        _cargar(tmp_path, {"x": {"max_budget_usd": 1}})


def test_bypass_exige_flag(tmp_path):
    d = tmp_path / "home" / "e"
    with pytest.raises(ErrorEmpresas, match="permitir_bypass"):
        _cargar(tmp_path, _empresa(d, permission_mode="bypassPermissions"))
    e = _cargar(tmp_path, _empresa(d, permission_mode="bypassPermissions", permitir_bypass=True))
    assert e["x"].permission_mode == "bypassPermissions"


def test_modo_desconocido_y_nombre_invalido(tmp_path):
    d = tmp_path / "home" / "e"
    with pytest.raises(ErrorEmpresas):
        _cargar(tmp_path, _empresa(d, permission_mode="todo"))
    with pytest.raises(ErrorEmpresas):
        _cargar(tmp_path, {"../x": {"directorio": str(d), "max_budget_usd": 1}})
    with pytest.raises(ErrorEmpresas):
        _cargar(tmp_path, {"jefe": {"directorio": str(d), "max_budget_usd": 1}})


def test_reglas_peligrosas():
    assert reglas_peligrosas(["Bash(python:*)", "Bash(npm:*)", "Bash(git:*)", "Bash", "Bash(*)"]) == [
        "Bash(python:*)", "Bash(npm:*)", "Bash(git:*)", "Bash", "Bash(*)"]
    assert reglas_peligrosas(["Bash(df -h)", "WebFetch", "Bash(uptime)"]) == []


def test_reglas_peligrosas_avisan(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        _cargar(tmp_path, _empresa(tmp_path / "home" / "e", allowed_tools=["Bash(python:*)"]))
    assert "arbitrario" in caplog.text


def test_ejemplo_valido_y_sin_reglas_genericas():
    datos = json.loads((REPO / "empresas.json.example").read_text(encoding="utf-8"))
    for conf in datos.values():
        assert reglas_peligrosas(conf.get("allowed_tools", [])) == []
        assert conf["max_budget_usd"] > 0
        assert conf.get("permission_mode") != "bypassPermissions"
    cargar_empresas(REPO / "empresas.json.example")  # con el home real y el repo real
