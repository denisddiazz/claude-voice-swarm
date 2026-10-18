"""Sesiones externas cuya conversación ya no existe: quedan caducadas, no se les encarga nada,
listar_sesiones lo muestra, y vuelven a estar activas cuando su carpeta informa de nuevo."""
import asyncio
import os
import time
import uuid

import pytest

import externas
import herramientas
import sesiones
from config import Config
from empresas import cargar_todas


def _sid():
    return str(uuid.uuid4())


def _transcripcion(directorio, sid):
    """Transcripción ya antigua: sin actividad reciente, así el puente la puede reanudar."""
    t = externas.transcripcion(directorio, sid)
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text("{}\n", encoding="utf-8")
    m = time.time() - 3600
    os.utime(t, (m, m))
    return t


def _informar(buzon, cwd, sid, mensaje="hola"):
    return herramientas.informar_externo(buzon, cargar_todas(buzon, Config.EMPRESAS_FILE), None,
                                         cwd, sid, "entorno", mensaje)


def _turno_externo(buzon, proyecto, sid, nombre="app"):
    return asyncio.run(sesiones.ejecutar_turno(buzon, nombre, "haz x", proyecto, [], 30,
                                               sid_fijo=sid, externa=True))


@pytest.fixture
def proyecto(tmp_path):
    d = tmp_path / "Mi Proyecto"
    d.mkdir()
    return d.resolve()


@pytest.fixture(autouse=True)
def sin_empresas_json(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "EMPRESAS_FILE", tmp_path / "empresas.json")


def _externa(buzon, proyecto, sid, nombre="app"):
    buzon.registrar_sesion(nombre, str(proyecto), "sesión principal", None, origen="externa", session_id=sid)


def test_sin_transcripcion_la_externa_queda_caducada_sin_llamar_a_claude(buzon, claude_falso, proyecto):
    sid = _sid()
    _externa(buzon, proyecto, sid)
    with pytest.raises(sesiones.ErrorSesion, match="ya no existe") as exc:
        _turno_externo(buzon, proyecto, sid)
    assert "/borrar_sesion app" in str(exc.value) and "vuelve a darla de alta" in str(exc.value)
    assert claude_falso.llamadas() == []
    assert buzon.sesion_caducada("app")


def test_caducada_no_se_reanuda_aunque_reaparezca_la_transcripcion(buzon, claude_falso, proyecto):
    sid = _sid()
    _externa(buzon, proyecto, sid)
    with pytest.raises(sesiones.ErrorSesion):
        _turno_externo(buzon, proyecto, sid)
    _transcripcion(proyecto, sid)
    claude_falso.configurar(existentes=[sid])
    with pytest.raises(sesiones.ErrorSesion, match="ya no existe"):
        _turno_externo(buzon, proyecto, sid)
    assert claude_falso.llamadas() == []


def test_no_conversation_found_de_claude_marca_caducada(buzon, claude_falso, proyecto):
    sid = _sid()
    _externa(buzon, proyecto, sid)
    _transcripcion(proyecto, sid)  # hay transcripción, pero claude no la encuentra
    claude_falso.configurar(existentes=[])
    with pytest.raises(sesiones.ErrorSesion, match="ya no existe"):
        _turno_externo(buzon, proyecto, sid)
    assert len(claude_falso.llamadas()) == 1
    assert buzon.sesion_caducada("app")


def test_una_sesion_con_session_id_de_empresas_json_no_se_caduca(buzon, claude_falso, proyecto):
    sid = _sid()  # empresas.json: no es externa, así que no se marca ni se comprueba la transcripción
    claude_falso.configurar(existentes=[sid])
    r = asyncio.run(sesiones.ejecutar_turno(buzon, "app", "haz x", proyecto, [], 30, sid_fijo=sid))
    assert r == "hecho"
    assert not buzon.sesion_caducada("app")


def test_no_se_le_encarga_nada_a_una_caducada(buzon, claude_falso, proyecto):
    sid = _sid()
    _externa(buzon, proyecto, sid)
    with pytest.raises(sesiones.ErrorSesion):
        _turno_externo(buzon, proyecto, sid)
    r = herramientas.encargar(buzon, cargar_todas(buzon, Config.EMPRESAS_FILE), None, "app", "orden", 0, 0)
    assert "No encargo nada" in r and "/borrar_sesion app" in r
    assert "app" not in buzon.destinos_pendientes()


def test_listar_sesiones_muestra_la_caducada(buzon, proyecto):
    sid = _sid()
    _externa(buzon, proyecto, sid)
    buzon.marcar_caducada("app")
    linea = herramientas.listar_sesiones(cargar_todas(buzon, Config.EMPRESAS_FILE), None)
    assert "app:" in linea and "CADUCADA" in linea


def test_informar_con_conversacion_nueva_reactiva_la_sesion(buzon, claude_falso, proyecto):
    viejo, nuevo = _sid(), _sid()
    _externa(buzon, proyecto, viejo)
    with pytest.raises(sesiones.ErrorSesion):
        _turno_externo(buzon, proyecto, viejo)
    _transcripcion(proyecto, nuevo)
    r = _informar(buzon, proyecto, nuevo)
    assert "pasa a ser la sesión 'app'" in r
    fila = buzon.externa_por_directorio(str(proyecto))
    assert fila["session_id"] == nuevo and not buzon.sesion_caducada("app")
    claude_falso.configurar(existentes=[nuevo])
    assert _turno_externo(buzon, proyecto, nuevo) == "hecho"
    argv = claude_falso.llamadas()[0]["argv"]
    assert argv[argv.index("--resume") + 1] == nuevo


def test_informar_con_la_misma_conversacion_tambien_reactiva(buzon, proyecto):
    sid = _sid()
    _externa(buzon, proyecto, sid)
    buzon.marcar_caducada("app")
    _transcripcion(proyecto, sid)
    r = _informar(buzon, proyecto, sid)
    assert "vuelve a estar activa" in r
    assert not buzon.sesion_caducada("app")
    assert buzon.externa_por_directorio(str(proyecto))["session_id"] == sid
