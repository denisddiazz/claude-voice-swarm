"""A6: una sesión que falla se recupera sin tocar la base de datos a mano."""
import asyncio

import pytest

import sesiones
from config import Config
from empresas import Empresa


def _turno(buzon, tmp_path, nombre="x", timeout=30):
    e = Empresa(nombre, "d", tmp_path / "trabajo", max_budget_usd=1)
    return asyncio.run(sesiones.ejecutar_turno(buzon, nombre, "orden", e.directorio, sesiones.args_empresa(e), timeout))


def test_primer_turno_sin_json_cambia_de_uuid(buzon, tmp_path, claude_falso):
    claude_falso.configurar(modos=["sin_json", "ok"])
    sid1, _ = buzon.sesion("x")
    with pytest.raises(sesiones.ErrorSesion, match="sesión nueva"):
        _turno(buzon, tmp_path)
    sid2, iniciada = buzon.sesion("x")
    assert sid2 != sid1 and not iniciada
    assert _turno(buzon, tmp_path) == "hecho"  # sin "already in use"
    assert buzon.sesion("x") == (sid2, True)


def test_primer_turno_con_error_no_se_marca_iniciada(buzon, tmp_path, claude_falso):
    claude_falso.configurar(modos=["error", "ok"])
    with pytest.raises(sesiones.ErrorSesion):
        _turno(buzon, tmp_path)
    assert buzon.sesion("x")[1] is False
    assert _turno(buzon, tmp_path) == "hecho"
    llamadas = claude_falso.llamadas()
    assert "--session-id" in llamadas[1]["argv"]  # no intenta --resume de algo que no existe


def test_sesion_corrupta_se_recrea_tras_n_fallos(buzon, tmp_path, claude_falso, monkeypatch):
    monkeypatch.setattr(Config, "SESION_MAX_FALLOS", 3)
    claude_falso.configurar(modos=["ok"])
    _turno(buzon, tmp_path)
    sid, iniciada = buzon.sesion("x")
    assert iniciada
    # La sesión desaparece del lado de Claude Code: --resume falla siempre.
    claude_falso.control.with_suffix(".estado.json").write_text('{"n": 0, "sesiones": []}', encoding="utf-8")
    for i in range(2):
        with pytest.raises(sesiones.ErrorSesion) as exc:
            _turno(buzon, tmp_path)
        assert not exc.value.recreada
        assert buzon.sesion("x") == (sid, True)
    with pytest.raises(sesiones.ErrorSesion) as exc:
        _turno(buzon, tmp_path)
    assert exc.value.recreada and "recreado" in str(exc.value)
    sid_nuevo, iniciada = buzon.sesion("x")
    assert sid_nuevo != sid and not iniciada
    assert _turno(buzon, tmp_path) == "hecho"


def test_exito_reinicia_contador(buzon, tmp_path, claude_falso, monkeypatch):
    monkeypatch.setattr(Config, "SESION_MAX_FALLOS", 2)
    claude_falso.configurar(modos=["ok", "error", "ok", "error", "ok"])
    for esperado_ok in (True, False, True, False, True):
        if esperado_ok:
            _turno(buzon, tmp_path)
        else:
            with pytest.raises(sesiones.ErrorSesion) as exc:
                _turno(buzon, tmp_path)
            assert not exc.value.recreada


def test_timeout_mata_el_proceso(buzon, tmp_path, claude_falso):
    claude_falso.configurar(modos=["dormir"])
    with pytest.raises(sesiones.ErrorSesion, match="superó"):
        _turno(buzon, tmp_path, timeout=2)
