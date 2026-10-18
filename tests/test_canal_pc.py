"""Canal PC: el jefe habla con el subjefe desde la ventana del PC o desde Telegram. Lo del subjefe
va al canal activo; al cambiar de canal se entrega en el nuevo lo que no vio en el anterior."""
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import herramientas
import rele_pc
import sesiones
from buzon import Buzon
from config import Config
from conftest import REPO

TITULO = "Jefe — ventana del subjefe en el PC"


@pytest.fixture
def main_mod(monkeypatch, tmp_path, claude_falso):
    for nombre in ("telegram", "telegram.ext", "comunicaciones", "voice_engine", "notificaciones"):
        monkeypatch.setitem(sys.modules, nombre, MagicMock())
    monkeypatch.delitem(sys.modules, "main", raising=False)
    import main

    monkeypatch.setattr(Config, "AUTHORIZED_CHAT_ID", 111)
    monkeypatch.setattr(Config, "EMPRESAS_FILE", tmp_path / "empresas.json")
    monkeypatch.setattr(rele_pc, "segundos_inactivo", lambda: None)
    monkeypatch.setattr(main, "responder_por_telegram", AsyncMock())
    monkeypatch.setattr(main, "puente", main.Puente())
    yield main
    sys.modules.pop("main", None)


def _telegram(main, texto, modo_voz=False):
    """Como handle_text / handle_voice: el jefe escribe por Telegram y el subjefe hace su turno."""
    upd = SimpleNamespace(effective_message=SimpleNamespace(set_reaction=AsyncMock()))
    asyncio.run(main._al_subjefe(upd, texto, modo_voz))
    asyncio.run(main.puente._trabajar("subjefe"))


def _pc(main, texto):
    """Como el MCP de la ventana del jefe (sin la clave del puente) y el turno que sigue."""
    out = herramientas.hablar_al_subjefe(Buzon(), Config.JEFE_PC_DIR, texto)
    asyncio.run(main.puente._trabajar("subjefe"))
    return out


def _sistema(main, texto="informe de una empresa"):
    """Un turno del subjefe sin mensaje del jefe (p. ej., llega un informe)."""
    main.puente.buzon.enviar("sistema", "subjefe", texto)
    asyncio.run(main.puente._trabajar("subjefe"))


def _reles(claude_falso):
    return [x for x in claude_falso.llamadas() if x["modo"].startswith("rele:")]


def _texto_rele(llamada) -> str:
    import re
    return re.search(r"<<<TEXTO-(\w+)>>>\n(.*)\n<<<FIN-\1>>>", llamada["stdin"], re.S).group(2)


def _telegramas(main) -> list[str]:
    return [c.args[0] for c in main.responder_por_telegram.await_args_list]


def _update():
    return SimpleNamespace(effective_chat=SimpleNamespace(id=111),
                           effective_message=SimpleNamespace(reply_text=AsyncMock()))


# --- enrutado por canal ---
def test_por_defecto_todo_va_a_telegram(main_mod, claude_falso):
    claude_falso.configurar(resultado="respuesta por el móvil")
    _telegram(main_mod, "hola")
    assert _telegramas(main_mod) == ["respuesta por el móvil"]
    assert _reles(claude_falso) == []
    assert main_mod.puente.buzon.estado_canal()["activo"] == "movil"


def test_desde_el_pc_la_respuesta_va_al_pc_por_el_rele(main_mod, claude_falso):
    claude_falso.configurar(resultado="respuesta en el PC", rele_coste=0.0031)
    assert "Entregado al subjefe" in _pc(main_mod, "¿qué tal va lo de marzo?")
    turno, rele = claude_falso.llamadas()
    assert 'origen="jefe"' in turno["stdin"] and "¿qué tal va lo de marzo?" in turno["stdin"]  # adoptado y firmado
    assert _texto_rele(rele) == "respuesta en el PC"
    assert f"Destinatario: {TITULO}" in rele["stdin"]
    assert main_mod.responder_por_telegram.await_count == 0
    buzon = main_mod.puente.buzon
    assert buzon.estado_canal()["activo"] == "pc"
    (salida,) = buzon.salidas()
    assert (salida["canal"], salida["texto"], salida["coste_usd"]) == ("pc", "respuesta en el PC", 0.0031)
    assert buzon.gasto_hoy() == pytest.approx(0.0031)


def test_el_rele_es_minimo(main_mod, claude_falso):
    _pc(main_mod, "hola")
    (rele,) = _reles(claude_falso)
    argv = rele["argv"]
    assert argv[argv.index("--model") + 1] == "haiku" and argv[argv.index("--effort") + 1] == "low"
    assert argv[argv.index("--tools") + 1] == "ListAgents,SendMessage,ToolSearch"
    assert argv[argv.index("--allowedTools") + 1:] == ["ListAgents", "SendMessage", "ToolSearch"]
    assert argv[argv.index("--setting-sources") + 1] == "project"
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--system-prompt", "--verbose"):
        assert flag in argv
    assert "--mcp-config" not in argv
    assert os.path.samefile(rele["cwd"], Config.DATA_DIR / "rele-pc")
    assert rele["env"].get("CLAUDE_VOICE_SWARM_PUENTE") == "1"
    assert "TELEGRAM_TOKEN" not in rele["env"]


def test_el_canal_persiste_tras_reiniciar(main_mod, claude_falso):
    _pc(main_mod, "hola")
    assert Buzon(Config.BUZON_DB, clave=b"otra" * 8).estado_canal()["activo"] == "pc"


# --- cambio por último canal y entrega de lo no visto ---
def test_de_pc_a_movil_por_telegram_entrega_lo_no_visto_en_el_pc(main_mod, claude_falso):
    claude_falso.configurar(resultado="R1 en el PC")
    _pc(main_mod, "empieza el informe")
    claude_falso.configurar(resultado="R2 por el móvil")
    _telegram(main_mod, "ya estoy fuera")
    recopilacion, r2 = _telegramas(main_mod)
    assert "Mientras no estabas en el PC" in recopilacion and "R1 en el PC" in recopilacion
    assert r2 == "R2 por el móvil"
    assert main_mod.puente.buzon.estado_canal()["activo"] == "movil"
    assert len(_reles(claude_falso)) == 1  # lo nuevo ya no va al PC


def test_de_movil_a_pc_entrega_en_una_sola_entrega_lo_enviado_al_movil(main_mod, claude_falso):
    claude_falso.configurar(resultado="R1 por el móvil")
    _telegram(main_mod, "prepara el informe")
    claude_falso.configurar(resultado="R2 informe listo")
    _sistema(main_mod)
    assert _telegramas(main_mod) == ["R1 por el móvil", "R2 informe listo"]
    claude_falso.configurar(resultado="R3 en el PC")
    _pc(main_mod, "ya estoy en el PC")
    (rele,) = _reles(claude_falso)
    texto = _texto_rele(rele)
    assert "Esto te lo envié al móvil" in texto
    assert texto.index("R1 por el móvil") < texto.index("R2 informe listo") < texto.index("R3 en el PC")
    # Lo recopilado no se vuelve a recopilar al volver al móvil
    claude_falso.configurar(resultado="R4")
    _telegram(main_mod, "vuelvo a la calle")
    recopilacion = _telegramas(main_mod)[2]
    assert "R3 en el PC" in recopilacion and "R1 por el móvil" not in recopilacion


def test_lo_ya_visto_en_el_movil_no_se_repite_en_el_pc(main_mod, claude_falso):
    claude_falso.configurar(resultado="R1 por el móvil")
    _telegram(main_mod, "hola")
    _telegram(main_mod, "visto, gracias")  # escribir por Telegram da por visto lo anterior
    main_mod.responder_por_telegram.reset_mock()
    claude_falso.configurar(resultado="R3 en el PC")
    _pc(main_mod, "ya estoy en el PC")
    texto = _texto_rele(_reles(claude_falso)[0])
    assert texto.count("R1 por el móvil") == 1  # solo la respuesta a "visto, gracias"
    assert texto.endswith("R3 en el PC")


# --- comandos ---
def test_comandos_desde_telegram(main_mod, claude_falso):
    upd = _update()
    asyncio.run(main_mod.cmd_pc(upd, None))
    assert "PC" in upd.effective_message.reply_text.await_args.args[0]
    assert main_mod.puente.buzon.estado_canal()["activo"] == "pc"
    upd = _update()
    asyncio.run(main_mod.cmd_canal(upd, None))
    assert upd.effective_message.reply_text.await_args.args[0].startswith("Canal activo: PC")
    asyncio.run(main_mod.cmd_movil(_update(), None))
    assert main_mod.puente.buzon.estado_canal()["activo"] == "movil"
    assert _reles(claude_falso) == []


def test_comandos_desde_el_pc(main_mod, claude_falso):
    buzon = Buzon()
    assert herramientas.hablar_al_subjefe(buzon, Config.JEFE_PC_DIR, "/pc").startswith("Canal activo: PC")
    assert herramientas.hablar_al_subjefe(buzon, Config.JEFE_PC_DIR, "/canal").startswith("Canal activo: PC")
    assert herramientas.cambiar_canal(buzon, Config.JEFE_PC_DIR, "movil").startswith("Canal activo: móvil")
    assert buzon.destinos_pendientes() == []  # los comandos no llegan al subjefe
    assert herramientas.cambiar_canal(buzon, Config.JEFE_PC_DIR, "fax").startswith("Canal desconocido")


def test_tras_pc_desde_telegram_lo_nuevo_va_al_pc(main_mod, claude_falso):
    claude_falso.configurar(resultado="R1")
    _telegram(main_mod, "hola")
    asyncio.run(main_mod.cmd_pc(_update(), None))
    claude_falso.configurar(resultado="informe de la empresa, resumido")
    _sistema(main_mod)
    (rele,) = _reles(claude_falso)
    assert _texto_rele(rele) == "informe de la empresa, resumido"  # R1 ya lo vio en Telegram


# --- inactividad ---
def _en_pc(main_mod, claude_falso):
    claude_falso.configurar(resultado="R1 en el PC")
    _pc(main_mod, "hola")
    return main_mod.puente.buzon.estado_canal()["actividad_pc"]


def test_inactividad_de_entrada_pasa_al_movil_con_lo_no_visto(main_mod, claude_falso, monkeypatch):
    actividad = _en_pc(main_mod, claude_falso)
    ahora = actividad + 16 * 60
    monkeypatch.setattr(rele_pc, "segundos_inactivo", lambda: 16 * 60 + 1)  # última tecla, antes de R1
    asyncio.run(main_mod.puente.vigilar_canal(ahora))
    estado = main_mod.puente.buzon.estado_canal()
    assert (estado["activo"], estado["motivo"]) == ("movil", "inactividad")
    (recopilacion,) = _telegramas(main_mod)
    assert "Mientras no estabas en el PC" in recopilacion and "R1 en el PC" in recopilacion


def test_actividad_reciente_mantiene_el_pc(main_mod, claude_falso, monkeypatch):
    actividad = _en_pc(main_mod, claude_falso)
    monkeypatch.setattr(rele_pc, "segundos_inactivo", lambda: 30)
    asyncio.run(main_mod.puente.vigilar_canal(actividad + 16 * 60))
    estado = main_mod.puente.buzon.estado_canal()
    assert estado["activo"] == "pc" and estado["actividad_pc"] == pytest.approx(actividad + 16 * 60 - 30)
    assert _telegramas(main_mod) == []


def test_sin_medida_de_entrada_cuenta_el_ultimo_mensaje_del_pc(main_mod, claude_falso):
    actividad = _en_pc(main_mod, claude_falso)
    asyncio.run(main_mod.puente.vigilar_canal(actividad + 14 * 60))
    assert main_mod.puente.buzon.estado_canal()["activo"] == "pc"
    asyncio.run(main_mod.puente.vigilar_canal(actividad + 15 * 60 + 1))
    assert main_mod.puente.buzon.estado_canal()["activo"] == "movil"


def test_fuente_mensajes_ignora_la_entrada_de_windows(main_mod, claude_falso, monkeypatch):
    monkeypatch.setattr(Config, "PC_INACTIVIDAD_FUENTE", "mensajes")
    monkeypatch.setattr(rele_pc, "segundos_inactivo", lambda: 0)  # algo simula entrada sin parar
    actividad = _en_pc(main_mod, claude_falso)
    asyncio.run(main_mod.puente.vigilar_canal(actividad + 15 * 60 + 1))
    assert main_mod.puente.buzon.estado_canal()["activo"] == "movil"


def test_pasar_al_pc_da_un_plazo_aunque_el_pc_lleve_rato_parado(main_mod, claude_falso, monkeypatch):
    monkeypatch.setattr(rele_pc, "segundos_inactivo", lambda: 3600)
    asyncio.run(main_mod.cmd_pc(_update(), None))
    asyncio.run(main_mod.puente.vigilar_canal(time.time() + 60))
    assert main_mod.puente.buzon.estado_canal()["activo"] == "pc"


def test_inactividad_cero_es_nunca(main_mod, claude_falso, monkeypatch):
    monkeypatch.setattr(Config, "PC_INACTIVIDAD_MIN", 0)
    actividad = _en_pc(main_mod, claude_falso)
    asyncio.run(main_mod.puente.vigilar_canal(actividad + 10 * 3600))
    assert main_mod.puente.buzon.estado_canal()["activo"] == "pc"


def test_segundos_inactivo_no_falla():
    valor = rele_pc.segundos_inactivo()
    assert valor is None or valor >= 0


# --- urgencias ---
def test_urgencia_va_tambien_al_pc_si_es_el_canal_activo(main_mod, claude_falso):
    claude_falso.configurar(modos=["alerta"], resultado="te he avisado al móvil")
    _pc(main_mod, "vigila el servidor")
    texto = _texto_rele(_reles(claude_falso)[0])
    assert "[URGENTE] Aviso del subjefe: se cae el servidor" in texto and "te he avisado al móvil" in texto
    assert main_mod.responder_por_telegram.await_count == 0  # al móvil ya fue por ntfy


def test_urgencia_con_canal_movil_no_se_duplica_en_telegram(main_mod, claude_falso):
    claude_falso.configurar(modos=["alerta"], resultado="te he avisado al móvil")
    _telegram(main_mod, "vigila el servidor")
    assert _telegramas(main_mod) == ["te he avisado al móvil"]
    assert _reles(claude_falso) == []


def test_urgencia_sin_respuesta_tambien_llega_al_pc(main_mod, claude_falso):
    claude_falso.configurar(modos=["alerta"], resultado=sesiones.SILENCIO)
    _pc(main_mod, "vigila el servidor")
    assert _texto_rele(_reles(claude_falso)[0]) == "[URGENTE] Aviso del subjefe: se cae el servidor"


# --- fallo del relé ---
@pytest.mark.parametrize("modo", ["ausente", "sin_json", "alterado", "otra_sesion"])
def test_si_el_rele_falla_va_a_telegram_con_aviso(main_mod, claude_falso, modo):
    claude_falso.configurar(resultado="R1 en el PC")
    _pc(main_mod, "hola")
    claude_falso.configurar(resultado="R2 importante", rele=modo)
    _sistema(main_mod)
    recopilacion, texto = _telegramas(main_mod)  # R1 tampoco lo vio: el PC no parece estar atendido
    assert "R1 en el PC" in recopilacion
    assert texto.startswith("(No pude entregarlo en el PC") and texto.endswith("R2 importante")
    estado = main_mod.puente.buzon.estado_canal()
    assert (estado["activo"], estado["motivo"]) == ("movil", "fallo_rele")


def test_si_el_rele_falla_tambien_llega_lo_no_visto_en_el_pc(main_mod, claude_falso):
    claude_falso.configurar(resultado="R1 en el PC")
    _pc(main_mod, "hola")
    claude_falso.configurar(resultado="R2", rele="ausente")
    _sistema(main_mod)
    recopilacion, aviso = _telegramas(main_mod)
    assert "R1 en el PC" in recopilacion and aviso.endswith("R2")


# --- voz solo en Telegram ---
def test_voz_solo_en_telegram(main_mod, claude_falso):
    claude_falso.configurar(resultado="respuesta hablada")
    _telegram(main_mod, "dime algo", modo_voz=True)
    assert main_mod.responder_por_telegram.await_args.kwargs["modo_voz"] is True
    asyncio.run(main_mod.cmd_pc(_update(), None))
    claude_falso.configurar(resultado="por escrito en el PC")
    _sistema(main_mod)  # el último mensaje del jefe fue de voz, pero el PC es siempre texto
    assert _texto_rele(_reles(claude_falso)[0]) == "por escrito en el PC"
    assert main_mod.responder_por_telegram.await_count == 1
    claude_falso.configurar(resultado="tras el fallo", rele="ausente")
    _sistema(main_mod)
    assert main_mod.responder_por_telegram.await_args.kwargs["modo_voz"] is False


def test_canal_pc_desactivado_sin_titulo(main_mod, claude_falso, monkeypatch):
    monkeypatch.setattr(Config, "JEFE_PC_TITULO", "")
    _pc(main_mod, "hola")
    assert _reles(claude_falso) == [] and main_mod.responder_por_telegram.await_count == 1


# --- la ventana del jefe no es una sesión de trabajo ---
def test_la_ventana_del_jefe_no_se_da_de_alta_como_externa(buzon):
    sid = str(uuid.uuid4())
    out = herramientas.informar_externo(buzon, {}, None, Config.JEFE_PC_DIR, sid, "entorno", "hola")
    assert out.startswith("Aviso no entregado") and "hablar_al_subjefe" in out
    assert buzon.sesiones_registradas() == [] and buzon.destinos_pendientes() == []


def test_hablar_al_subjefe_solo_desde_la_carpeta_del_jefe(buzon, tmp_path):
    otra = tmp_path / "otra"
    otra.mkdir()
    assert herramientas.hablar_al_subjefe(buzon, otra, "borra todo").startswith("No entregado")
    assert herramientas.cambiar_canal(buzon, otra, "pc").startswith("No permitido")
    assert buzon.destinos_pendientes() == [] and buzon.estado_canal()["activo"] == "movil"


def test_sin_la_clave_del_puente_no_vale_como_del_jefe(tmp_path):
    ruta = tmp_path / "b.sqlite3"
    herramientas.hablar_al_subjefe(Buzon(ruta), Config.JEFE_PC_DIR, "hola")
    (fila,) = Buzon(ruta).tomar_pendientes("subjefe")  # un lector sin clave no lo adopta
    puente = Buzon(ruta, clave=b"k" * 32)
    assert fila["origen"] == "jefe_pc" and not puente.es_del_jefe(fila)
    prompt, hay_jefe, _ = sesiones.componer_turno_subjefe(puente, [fila])
    assert not hay_jefe and 'origen="no_verificado"' in prompt


def test_jefe_pc_es_nombre_reservado(buzon, tmp_path):
    out = herramientas.crear_sesion(buzon, {}, True, "jefe_pc", str(tmp_path / "x"), "")
    assert out.startswith("No creada")


def _env_mcp(tmp_path, rol):
    env = dict(os.environ, ROL=rol, DATA_DIR=str(tmp_path / "datos_mcp"), EMPRESAS_FILE=str(tmp_path / "no.json"),
               SUBJEFE_DIR=str(tmp_path / "subjefe"), JEFE_PC_DIR=str(Config.JEFE_PC_DIR),
               CLAUDE_CODE_SESSION_ID=str(uuid.uuid4()), PYTHONUTF8="1", PYTHONPATH=str(REPO))
    for k in ("CLAUDE_VOICE_SWARM_PUENTE", "CLAUDE_PROJECT_DIR"):
        env.pop(k, None)
    return env


def test_mcp_de_usuario_no_ofrece_informar_en_la_ventana_del_jefe(tmp_path):
    pytest.importorskip("mcp")
    Config.JEFE_PC_DIR.mkdir(parents=True, exist_ok=True)
    out = subprocess.run([sys.executable, "-c", "import mcp_server; print(hasattr(mcp_server, 'informar_al_subjefe'))"],
                         cwd=Config.JEFE_PC_DIR, env=_env_mcp(tmp_path, "externa"), capture_output=True, text=True,
                         check=True).stdout
    assert out.strip() == "False"


def test_mcp_jefe_pc_entrega_desde_su_carpeta(tmp_path):
    pytest.importorskip("mcp")
    Config.JEFE_PC_DIR.mkdir(parents=True, exist_ok=True)
    codigo = ("import asyncio, mcp_server; print(hasattr(mcp_server, 'informar_al_subjefe')); "
              "print(asyncio.run(mcp_server.hablar_al_subjefe('hola desde el PC')))")
    out = subprocess.run([sys.executable, "-c", codigo], cwd=Config.JEFE_PC_DIR, env=_env_mcp(tmp_path, "jefe_pc"),
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert out.splitlines()[0] == "False" and "Entregado al subjefe" in out
    b = Buzon(tmp_path / "datos_mcp" / "buzon.sqlite3", clave=b"k" * 32)
    (fila,) = b.tomar_pendientes("subjefe")
    assert (fila["origen"], fila["canal"], fila["texto"]) == ("jefe", "pc", "hola desde el PC") and b.es_del_jefe(fila)
    assert b.estado_canal()["activo"] == "pc"


def test_preparar_carpeta_del_jefe(tmp_path):
    d = Config.JEFE_PC_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "CLAUDE.md").write_text("mío", encoding="utf-8")
    (d / ".mcp.json").write_text(json.dumps({"mcpServers": {"otro": {"command": "x"}}}), encoding="utf-8")
    sesiones.preparar_carpeta_jefe_pc()
    conf = json.loads((d / ".mcp.json").read_text(encoding="utf-8"))
    assert conf["mcpServers"]["otro"] == {"command": "x"}
    jefe = conf["mcpServers"]["jefe"]
    assert jefe["env"]["ROL"] == "jefe_pc" and jefe["env"]["JEFE_PC_DIR"] == str(d)
    assert jefe["args"][0].endswith("mcp_server.py")
    assert (d / "CLAUDE.md").read_text(encoding="utf-8") == "mío"


# --- verificación de la salida del relé ---
def _lineas(to=TITULO, message="hola", resultado=None, extra=None):
    resultado = resultado or {"type": "tool_result", "tool_use_id": "t1", "content": "Message sent."}
    usuario = {"type": "user", "message": {"content": [resultado]}}
    if extra is not None:
        usuario["tool_use_result"] = extra
    return [json.dumps(x) for x in (
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "SendMessage", "input": {"to": to, "message": message}}]}},
        usuario, {"type": "result", "result": '{"ok": true}', "total_cost_usd": 0.004})]


def test_analizar_salida_del_rele():
    assert rele_pc.analizar_salida(_lineas(), "hola", TITULO).ok
    assert rele_pc.analizar_salida(_lineas(to=TITULO + " [614214]"), "hola", TITULO).ok
    assert rele_pc.analizar_salida(_lineas(message="hola\r\n"), "hola", TITULO).ok
    assert rele_pc.analizar_salida(_lineas(resultado={"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                                           extra={"success": True}), "hola", TITULO).ok
    r = rele_pc.analizar_salida(_lineas(message="hola, resumido"), "hola", TITULO)
    assert not r.ok and "alteró" in r.motivo and r.coste_usd == 0.004
    assert not rele_pc.analizar_salida(_lineas(to="Otra"), "hola", TITULO).ok
    malo = {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "No session named X"}
    assert not rele_pc.analizar_salida(_lineas(resultado=malo), "hola", TITULO).ok
    # El modelo dice que fue bien, pero no hay ninguna llamada a SendMessage: no cuenta
    solo_texto = [json.dumps({"type": "result", "result": '{"ok": true}', "total_cost_usd": 0.001})]
    assert not rele_pc.analizar_salida(solo_texto, "hola", TITULO).ok
