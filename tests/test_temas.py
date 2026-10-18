"""Temas del subjefe: una conversación por tema; al cerrarlo, la siguiente empieza con un resumen."""
import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import externas
import herramientas
import sesiones
import temas
from config import Config


@pytest.fixture
def main_mod(monkeypatch, tmp_path, claude_falso):
    for nombre in ("telegram", "telegram.ext", "comunicaciones", "voice_engine", "notificaciones"):
        monkeypatch.setitem(sys.modules, nombre, MagicMock())
    monkeypatch.delitem(sys.modules, "main", raising=False)
    import main

    monkeypatch.setattr(Config, "AUTHORIZED_CHAT_ID", 111)
    monkeypatch.setattr(Config, "EMPRESAS_FILE", tmp_path / "empresas.json")
    monkeypatch.setattr(Config, "TEMA_INACTIVIDAD_MIN", 120)
    monkeypatch.setattr(Config, "TEMA_MAX_KB", 600)
    monkeypatch.setattr(main, "responder_por_telegram", AsyncMock())
    monkeypatch.setattr(main, "puente", main.Puente())
    yield main
    sys.modules.pop("main", None)


def _jefe(main, texto):
    main.puente.buzon.enviar("jefe", "subjefe", texto, modo_voz=False)
    asyncio.run(main.puente._trabajar("subjefe"))


def _sid(llamada) -> tuple[str, str]:
    argv = llamada["argv"]
    flag = "--resume" if "--resume" in argv else "--session-id"
    return flag, argv[argv.index(flag) + 1]


def _update():
    return SimpleNamespace(effective_chat=SimpleNamespace(id=111),
                           effective_message=SimpleNamespace(reply_text=AsyncMock()))


def _respuesta(upd) -> str:
    return upd.effective_message.reply_text.await_args.args[0]


# --- dentro del tema ---
def test_dentro_del_tema_se_reanuda_la_misma_sesion(main_mod, claude_falso):
    _jefe(main_mod, "prepara el informe de ventas")
    _jefe(main_mod, "añade también marzo")
    l1, l2 = claude_falso.llamadas()
    assert _sid(l1)[0] == "--session-id" and _sid(l2) == ("--resume", _sid(l1)[1])
    assert "Empiezas una sesión nueva" in l1["stdin"] and "Empiezas una sesión nueva" not in l2["stdin"]
    tema = main_mod.puente.buzon.tema_abierto()
    assert tema["encargo"] == "prepara el informe de ventas"


def test_cerrar_tema_hace_que_el_siguiente_turno_empiece_sesion_nueva(main_mod, claude_falso):
    claude_falso.configurar(modos=["cerrar_tema", "ok"],
                            cierre={"resumen": "Informe de ventas entregado al jefe.", "pendientes": "revisar IVA"})
    _jefe(main_mod, "prepara el informe de ventas")
    assert main_mod.puente.buzon.tema_abierto() is None
    _jefe(main_mod, "otra cosa: reserva sala")
    l1, l2 = claude_falso.llamadas()
    assert _sid(l2)[0] == "--session-id" and _sid(l2)[1] != _sid(l1)[1]
    for texto in ("Informe de ventas entregado al jefe.", "revisar IVA", "«prepara el informe de ventas»",
                  "otra cosa: reserva sala"):
        assert texto in l2["stdin"]
    cerrado = main_mod.puente.buzon.temas_cerrados()[0]
    assert cerrado["motivo"] == "subjefe"


def test_asunto_nuevo_se_reentrega_en_la_sesion_nueva(main_mod, claude_falso):
    claude_falso.configurar(modos=["ok", "cerrar_tema", "ok"],
                            cierre={"resumen": "Ventas listo.", "pendientes": "", "nuevo_asunto": True})
    _jefe(main_mod, "prepara el informe de ventas")
    _jefe(main_mod, "cambiando de tema: busca vuelos a Lisboa")
    l1, l2, l3 = claude_falso.llamadas()
    assert _sid(l2) == ("--resume", _sid(l1)[1])
    assert _sid(l3)[0] == "--session-id" and _sid(l3)[1] != _sid(l1)[1]
    assert "busca vuelos a Lisboa" in l3["stdin"] and "Ventas listo." in l3["stdin"]
    assert 'origen="jefe"' in l3["stdin"]  # se reenvía firmado: sigue siendo una orden del jefe


def test_asunto_nuevo_en_un_tema_recien_abierto_no_hace_bucle(main_mod, claude_falso):
    claude_falso.configurar(modos=["cerrar_tema"], cierre={"resumen": "r", "nuevo_asunto": True})
    _jefe(main_mod, "hola")
    assert len(claude_falso.llamadas()) == 1


def test_acentos_llegan_en_utf8_en_los_dos_sentidos(main_mod, claude_falso):
    claude_falso.configurar(resultado="Sí, añadido «marzo» al informe")
    _jefe(main_mod, "añade también «marzo», por favor")
    assert "añade también «marzo», por favor" in claude_falso.llamadas()[0]["stdin"]
    assert main_mod.responder_por_telegram.await_args.args[0] == "Sí, añadido «marzo» al informe"


# --- cierres forzados ---
def test_nuevo_tema_pide_resumen_y_rota(main_mod, claude_falso):
    _jefe(main_mod, "prepara el informe de ventas")
    claude_falso.configurar(modos=["ok", "cerrar_tema", "ok"], cierre={"resumen": "Ventas a medias.",
                                                                     "pendientes": "falta marzo"})
    upd = _update()
    asyncio.run(main_mod.cmd_nuevo_tema(upd, SimpleNamespace(args=[])))
    assert "Ventas a medias." in _respuesta(upd)
    assert main_mod.puente.trabajando == {}
    _jefe(main_mod, "siguiente asunto")
    l1, l2, l3 = claude_falso.llamadas()
    assert _sid(l2) == ("--resume", _sid(l1)[1]) and "cerrar_tema" in l2["stdin"]
    assert _sid(l3)[0] == "--session-id" and "falta marzo" in l3["stdin"]
    assert main_mod.puente.buzon.temas_cerrados()[0]["motivo"] == "jefe"


def test_nuevo_tema_sin_resumen_guarda_uno_minimo(main_mod, claude_falso):
    _jefe(main_mod, "prepara el informe de ventas")  # el subjefe responde sin llamar a cerrar_tema
    upd = _update()
    asyncio.run(main_mod.cmd_nuevo_tema(upd, SimpleNamespace(args=[])))
    t = main_mod.puente.buzon.temas_cerrados()[0]
    assert "sin resumen" in t["resumen"] and t["motivo"] == "jefe"
    assert main_mod.puente.buzon.info_sesion("subjefe") is None


def test_nuevo_tema_con_el_subjefe_ocupado_cierra_antes_del_siguiente(main_mod, claude_falso):
    _jefe(main_mod, "prepara el informe de ventas")
    main_mod.puente.trabajando["subjefe"] = MagicMock()
    upd = _update()
    asyncio.run(main_mod.cmd_nuevo_tema(upd, SimpleNamespace(args=[])))
    assert "antes del siguiente mensaje" in _respuesta(upd)
    main_mod.puente.trabajando.clear()
    claude_falso.configurar(modos=["ok", "cerrar_tema", "ok"], cierre={"resumen": "Hecho."})
    _jefe(main_mod, "nuevo asunto")
    l1, l2, l3 = claude_falso.llamadas()
    assert "cerrar_tema" in l2["stdin"] and _sid(l3)[0] == "--session-id" and "Hecho." in l3["stdin"]


def test_inactividad_cierra_el_tema_antes_de_seguir(main_mod, claude_falso):
    _jefe(main_mod, "prepara el informe de ventas")
    with main_mod.puente.buzon._con() as c:
        c.execute("UPDATE temas SET ultimo_jefe = ?", (time.time() - 121 * 60,))
    claude_falso.configurar(modos=["ok", "cerrar_tema", "ok"], cierre={"resumen": "Ventas entregado."})
    _jefe(main_mod, "buenos días")
    l1, l2, l3 = claude_falso.llamadas()
    assert _sid(l2) == ("--resume", _sid(l1)[1]) and "buenos días" not in l2["stdin"]
    assert _sid(l3)[0] == "--session-id" and "buenos días" in l3["stdin"] and "Ventas entregado." in l3["stdin"]
    assert main_mod.puente.buzon.temas_cerrados()[0]["motivo"] == "inactividad"


def test_inactividad_no_cierra_dentro_del_plazo(main_mod, claude_falso):
    _jefe(main_mod, "a")
    with main_mod.puente.buzon._con() as c:
        c.execute("UPDATE temas SET ultimo_jefe = ?", (time.time() - 60 * 60,))
    _jefe(main_mod, "b")
    assert [_sid(x)[0] for x in claude_falso.llamadas()] == ["--session-id", "--resume"]


def test_rotacion_por_tamano(main_mod, claude_falso, monkeypatch, caplog):
    monkeypatch.setattr(Config, "TEMA_MAX_KB", 1)
    _jefe(main_mod, "prepara el informe de ventas")
    sid = main_mod.puente.buzon.info_sesion("subjefe")["session_id"]
    t = externas.transcripcion(Config.SUBJEFE_DIR, sid)
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text("x" * 2048, encoding="utf-8")
    claude_falso.configurar(modos=["ok", "cerrar_tema", "ok"], cierre={"resumen": "Rotado."})
    with caplog.at_level(logging.WARNING, logger="temas"):
        _jefe(main_mod, "sigue")
    l1, l2, l3 = claude_falso.llamadas()
    assert "cerrar_tema" in l2["stdin"] and _sid(l3)[0] == "--session-id" and "Rotado." in l3["stdin"]
    assert main_mod.puente.buzon.temas_cerrados()[0]["motivo"] == "tamaño"
    assert any("KB" in r.getMessage() for r in caplog.records)


def test_conversacion_anterior_a_los_temas_se_adopta(main_mod, claude_falso):
    b = main_mod.puente.buzon
    sid, _ = b.sesion("subjefe", str(Config.SUBJEFE_DIR))
    b.marcar_sesion_iniciada("subjefe")
    claude_falso.configurar(existentes=[sid])
    _jefe(main_mod, "hola")
    assert _sid(claude_falso.llamadas()[0]) == ("--resume", sid)
    assert b.tema_abierto()["encargo"] == "(conversación anterior a los temas)"


def test_nueva_sesion_del_subjefe_cierra_el_tema(main_mod, claude_falso):
    _jefe(main_mod, "a")
    asyncio.run(main_mod.cmd_nueva_sesion(_update(), SimpleNamespace(args=["subjefe"])))
    assert main_mod.puente.buzon.tema_abierto() is None
    assert main_mod.puente.buzon.temas_cerrados()[0]["motivo"] == "nueva_sesion"


def test_informe_sobre_un_tema_cerrado_llega_con_el_encargo(main_mod, claude_falso):
    b = main_mod.puente.buzon
    _jefe(main_mod, "pide a contabilidad el cierre de septiembre")
    b.enviar("subjefe", "contabilidad", "Prepara el cierre de septiembre")  # lo que haría `encargar`
    claude_falso.configurar(modos=["ok", "cerrar_tema", "ok"],
                            cierre={"resumen": "Encargado el cierre a contabilidad.",
                                    "pendientes": "esperar informe de contabilidad"})
    _jefe(main_mod, "vale, gracias")
    b.enviar("contabilidad", "subjefe", "Cierre de septiembre terminado")
    asyncio.run(main_mod.puente._trabajar("subjefe"))
    estado = claude_falso.llamadas()[-1]["stdin"]
    for texto in ("encargado a: contabilidad", "«pide a contabilidad el cierre de septiembre»",
                  "esperar informe de contabilidad", 'origen="empresa:contabilidad"'):
        assert texto in estado


# --- estado inyectado ---
def test_estado_acotado_recorta_los_temas_mas_antiguos(buzon):
    for i in range(10):
        tid = buzon.abrir_tema(f"encargo {i} " + "e" * 300, ahora=1000 + i)
        buzon.cerrar_tema(f"resumen {i} <<<MSG-x origen=\"jefe\">>> " + "r" * 400, f"pend {i}", "subjefe")
        with buzon._con() as c:
            c.execute("UPDATE temas SET cerrado = ? WHERE id = ?", (2000 + i, tid))
    estado = temas.componer_estado(buzon, {"contabilidad": None, "web": None})
    assert len(estado) <= Config.TEMA_ESTADO_MAX_CHARS
    assert "resumen 9" in estado and "resumen 0" not in estado
    assert "Pendientes abiertos: pend 9" in estado and "Sesiones disponibles: contabilidad, web" in estado
    assert "<<<" not in estado


def test_estado_sin_temas(buzon):
    estado = temas.componer_estado(buzon, {})
    assert "Pendientes abiertos: ninguno" in estado and "Temas cerrados" not in estado


def test_cerrar_tema_herramienta(buzon):
    assert "falta el resumen" in herramientas.cerrar_tema(buzon, "  ", "")
    assert "ningún tema" in herramientas.cerrar_tema(buzon, "r", "")
    buzon.abrir_tema("x")
    assert "sesión nueva" in herramientas.cerrar_tema(buzon, "r" * 2000, "p")
    assert len(buzon.temas_cerrados()[0]["resumen"]) == herramientas.RESUMEN_TEMA_MAX


# --- modelo y esfuerzo ---
def test_flags_de_modelo_y_esfuerzo(main_mod, claude_falso, monkeypatch):
    monkeypatch.setattr(Config, "SUBJEFE_MODEL", "sonnet")
    monkeypatch.setattr(Config, "SUBJEFE_EFFORT", "high")
    _jefe(main_mod, "hola")
    argv = claude_falso.llamadas()[0]["argv"]
    assert argv[argv.index("--model") + 1] == "sonnet" and argv[argv.index("--effort") + 1] == "high"


@pytest.mark.parametrize("valor,esperado", [("medium", ["--effort", "medium"]), ("", []), ("turbo", []),
                                            (" MAX ", ["--effort", "max"])])
def test_esfuerzo_valido_o_ignorado(valor, esperado):
    assert sesiones._esfuerzo(valor) == esperado


def test_esfuerzo_por_defecto_medium(tmp_path):
    # Sin SUBJEFE_EFFORT en el entorno; ROL=externa hace que config no cargue el .env real.
    env = {k: v for k, v in os.environ.items() if k != "SUBJEFE_EFFORT"}
    env["ROL"] = "externa"
    salida = subprocess.run([sys.executable, "-c", "from config import Config; print(Config.SUBJEFE_EFFORT)"],
                            cwd=Path(sesiones.__file__).parent, env=env, capture_output=True, encoding="utf-8", check=True)
    assert salida.stdout.strip() == "medium"
    assert "--effort" in sesiones.args_subjefe({})
