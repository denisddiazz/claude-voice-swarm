"""A7: presupuesto por turno obligatorio, tope diario global y límite de rebotes."""
import asyncio
import time

import pytest

import herramientas
import sesiones
from config import Config
from empresas import Empresa


def _valor(args, flag):
    return args[args.index(flag) + 1]


def test_presupuesto_por_turno_en_subjefe_y_empresas(monkeypatch):
    monkeypatch.setattr(Config, "SUBJEFE_MAX_BUDGET_USD", 0.4)
    monkeypatch.setattr(Config, "EMPRESA_MAX_BUDGET_USD", 1.5)
    assert _valor(sesiones.args_subjefe({}), "--max-budget-usd") == "0.4"
    e = Empresa("x", "d", Config.DATA_DIR / "x", max_budget_usd=2.5)
    assert _valor(sesiones.args_empresa(e), "--max-budget-usd") == "2.5"
    sin_propio = Empresa("y", "d", Config.DATA_DIR / "y")
    assert _valor(sesiones.args_empresa(sin_propio), "--max-budget-usd") == "1.5"


def test_presupuesto_cero_es_sin_tope(monkeypatch):
    monkeypatch.setattr(Config, "SUBJEFE_MAX_BUDGET_USD", 0)
    monkeypatch.setattr(Config, "EMPRESA_MAX_BUDGET_USD", 0)
    assert "--max-budget-usd" not in sesiones.args_subjefe({})
    assert "--max-budget-usd" not in sesiones.args_empresa(Empresa("x", "d", Config.DATA_DIR / "x"))
    # Un 0 explícito en empresas.json gana al valor de .env.
    monkeypatch.setattr(Config, "EMPRESA_MAX_BUDGET_USD", 3)
    assert "--max-budget-usd" not in sesiones.args_empresa(Empresa("x", "d", Config.DATA_DIR / "x", max_budget_usd=0))


def test_valores_por_defecto_sin_tope():
    import config
    assert config._float("NO_EXISTE_XYZ", "0") == 0


def test_coste_se_registra_y_activa_el_tope(buzon, tmp_path, claude_falso):
    claude_falso.configurar(modos=["ok"], coste=0.75)
    e = Empresa("x", "d", tmp_path / "t")
    for _ in range(2):
        asyncio.run(sesiones.ejecutar_turno(buzon, "x", "o", e.directorio, sesiones.args_empresa(e), 30))
    assert buzon.gasto_hoy() == pytest.approx(1.5)
    assert not buzon.tope_alcanzado(2)
    assert buzon.tope_alcanzado(1.5)
    assert not buzon.tope_alcanzado(0)  # 0 = sin tope


def test_coste_de_turno_cortado_por_presupuesto_cuenta(buzon, tmp_path, claude_falso):
    claude_falso.configurar(modos=["error_max_budget"], coste=1.02)
    e = Empresa("x", "d", tmp_path / "t")
    with pytest.raises(sesiones.ErrorSesion, match="límite"):
        asyncio.run(sesiones.ejecutar_turno(buzon, "x", "o", e.directorio, sesiones.args_empresa(e), 30))
    assert buzon.gasto_hoy() == pytest.approx(1.02)
    assert buzon.sesion("x")[1] is True  # el turno corrió: la sesión existe


def test_gasto_de_ayer_no_cuenta(buzon):
    buzon.registrar_gasto("x", 5)
    assert buzon.gasto_hoy(ahora=time.time() + 2 * 86400) == 0


def test_limite_de_rebotes(buzon):
    empresas = {"codigo": Empresa("codigo", "d", Config.DATA_DIR / "c")}
    assert "entregada" in herramientas.encargar(buzon, empresas, None, "codigo", "z", 50, 0)  # 0 = sin límite
    buzon.tomar_pendientes("codigo")
    assert "entregada" in herramientas.encargar(buzon, empresas, None, "codigo", "a", 0, 3)
    assert "entregada" in herramientas.encargar(buzon, empresas, None, "codigo", "b", 2, 3)
    r = herramientas.encargar(buzon, empresas, None, "codigo", "c", 3, 3)
    assert "Límite" in r
    filas = buzon.tomar_pendientes("codigo")
    assert [f["saltos"] for f in filas] == [1, 3]


def test_saltos_se_propagan_y_el_jefe_los_reinicia(buzon):
    buzon.enviar("codigo", "subjefe", "informe", saltos=2)
    _, _, saltos = sesiones.componer_turno_subjefe(buzon, buzon.tomar_pendientes("subjefe"))
    assert saltos == 2
    buzon.enviar("codigo", "subjefe", "informe", saltos=2)
    buzon.enviar("jefe", "subjefe", "sigue")
    _, hay_jefe, saltos = sesiones.componer_turno_subjefe(buzon, buzon.tomar_pendientes("subjefe"))
    assert hay_jefe and saltos == 0


def test_saltos_llegan_al_servidor_mcp(monkeypatch):
    import json
    monkeypatch.setattr(Config, "MAX_REBOTES", 4)
    args = sesiones.args_subjefe({}, saltos=2)
    env = json.loads(open(_valor(args, "--mcp-config"), encoding="utf-8").read())["mcpServers"]["buzon"]["env"]
    assert env["SALTOS"] == "2" and env["MAX_REBOTES"] == "4" and env["ROL"] == "subjefe"
    assert env["PERMITIR_CREAR"] == "0"
