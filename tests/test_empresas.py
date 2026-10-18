"""empresas.json: sustituciones opcionales; la única carpeta prohibida es la del puente y la de datos/."""
import json

import pytest

import sesiones
from conftest import REPO
from empresas import ErrorEmpresas, cargar_empresas, cargar_todas


def _cargar(tmp_path, conf):
    ruta = tmp_path / "empresas.json"
    ruta.write_text(json.dumps(conf), encoding="utf-8")
    return cargar_empresas(ruta, protegidas=[tmp_path / "repo", tmp_path / "repo" / "datos"])


def test_sin_sustituciones_no_se_pasan_permisos(tmp_path):
    e = _cargar(tmp_path, {"x": {"descripcion": "d", "directorio": str(tmp_path / "x")}})["x"]
    args = sesiones.args_empresa(e)
    assert "--permission-mode" not in args
    assert "--setting-sources" not in args
    assert "--model" not in args
    assert "--strict-mcp-config" not in args
    assert args[args.index("--allowedTools") + 1:] == ["mcp__buzon"]  # solo la herramienta del puente


def test_sustituciones_opcionales(tmp_path):
    conf = {"x": {"directorio": str(tmp_path / "x"), "modelo": "sonnet", "permission_mode": "bypassPermissions",
                  "allowed_tools": ["Bash(python:*)"], "setting_sources": "project"}}
    args = sesiones.args_empresa(_cargar(tmp_path, conf)["x"])
    assert args[args.index("--model") + 1] == "sonnet"
    assert args[args.index("--permission-mode") + 1] == "bypassPermissions"
    assert args[args.index("--setting-sources") + 1] == "project"
    assert "Bash(python:*)" in args


@pytest.mark.parametrize("rel", ["home", ".", "otra"])
def test_carpetas_cualesquiera_se_aceptan(tmp_path, rel):
    _cargar(tmp_path, {"x": {"directorio": str(tmp_path / rel)}})


@pytest.mark.parametrize("rel", ["repo", "repo/datos", "repo/sub"])
def test_rechaza_la_carpeta_del_puente_y_datos(tmp_path, rel):
    with pytest.raises(ErrorEmpresas, match="puente"):
        _cargar(tmp_path, {"x": {"directorio": str(tmp_path / rel)}})


def test_carpeta_real_del_puente_rechazada_por_defecto(tmp_path):
    ruta = tmp_path / "e.json"
    ruta.write_text(json.dumps({"x": {"directorio": str(REPO)}}), encoding="utf-8")
    with pytest.raises(ErrorEmpresas):
        cargar_empresas(ruta)


def test_nombres_y_campos_invalidos(tmp_path):
    d = str(tmp_path / "e")
    for conf in ({"../x": {"directorio": d}}, {"jefe": {"directorio": d}}, {"x": {}},
                 {"x": {"directorio": d, "permission_mode": "todo"}},
                 {"x": {"directorio": d, "max_budget_usd": -1}}):
        with pytest.raises(ErrorEmpresas):
            _cargar(tmp_path, conf)


def test_registradas_se_suman_y_empresas_json_manda(tmp_path, buzon):
    ruta = tmp_path / "empresas.json"
    ruta.write_text(json.dumps({"a": {"directorio": str(tmp_path / "a")}}), encoding="utf-8")
    buzon.registrar_sesion("b", str(tmp_path / "b"), "desc b", "haiku")
    buzon.registrar_sesion("a", str(tmp_path / "otra"), "", None)
    todas = cargar_todas(buzon, ruta, protegidas=[tmp_path / "repo"])
    assert set(todas) == {"a", "b"}
    assert todas["a"].directorio == (tmp_path / "a").resolve()
    assert todas["b"].modelo == "haiku" and todas["b"].origen == "creada"


def test_ejemplo_valido():
    cargar_empresas(REPO / "empresas.json.example")
