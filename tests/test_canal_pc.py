"""Canal PC: el jefe habla directamente con el subjefe en una sesión de la app abierta en SUBJEFE_DIR.
El hook UserPromptSubmit marca el canal y pone al día al subjefe del PC; lo que llega para el subjefe
mientras el canal es PC va a esa sesión por el relé; al volver al móvil, el headless continúa la
conversación del PC con --fork-session."""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import contexto_pc
import externas
import guarda_rele
import herramientas
import rele_pc
import sesiones
from buzon import Buzon
from config import Config
from conftest import REPO

PC_SID = "11111111-2222-4333-8444-555555555555"


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
    claude_falso.configurar(existentes=[PC_SID])
    yield main
    sys.modules.pop("main", None)


# --- ayudas ---
def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def _transcripcion_pc(*mensajes, sid=PC_SID):
    """Añade (rol, texto, instante) a la transcripción de la sesión del PC, donde la buscaría claude -p."""
    t = externas.transcripcion(Config.SUBJEFE_DIR, sid)
    t.parent.mkdir(parents=True, exist_ok=True)
    with open(t, "a", encoding="utf-8") as f:
        for rol, texto, cuando in mensajes:
            f.write(json.dumps({"type": rol, "timestamp": _iso(cuando),
                                "message": {"role": rol, "content": [{"type": "text", "text": texto}]}}) + "\n")
    return t


def _hook(texto, sid=PC_SID, transcript=None, cwd=None):
    """Como el hook UserPromptSubmit de la sesión de la app (proceso aparte, sin la clave del puente)."""
    transcript = transcript or externas.transcripcion(Config.SUBJEFE_DIR, sid)
    return contexto_pc.al_escribir(Buzon(), {"cwd": str(cwd or Config.SUBJEFE_DIR), "prompt": texto,
                                             "session_id": sid, "transcript_path": str(transcript)})


def _contexto(salida) -> str:
    return salida["hookSpecificOutput"]["additionalContext"] if salida else ""


def _telegram(main, texto, modo_voz=False):
    upd = SimpleNamespace(effective_message=SimpleNamespace(set_reaction=AsyncMock()))
    asyncio.run(main._al_subjefe(upd, texto, modo_voz))
    asyncio.run(main.puente._trabajar("subjefe"))


def _informe(main, texto="informe de ventas listo", origen="ventas", saltos=1):
    main.puente.buzon.enviar(origen, "subjefe", texto, saltos=saltos)
    asyncio.run(main.puente._trabajar("subjefe"))


def _reles(claude_falso):
    return [x for x in claude_falso.llamadas() if x["modo"].startswith("rele:")]


def _turnos(claude_falso):
    return [x for x in claude_falso.llamadas() if not x["modo"].startswith("rele:")]


def _texto_rele(llamada) -> str:
    return re.search(r"<<<TEXTO-(\w+)>>>\n(.*)\n<<<FIN-\1>>>", llamada["stdin"], re.S).group(2)


def _telegramas(main) -> list[str]:
    return [c.args[0] for c in main.responder_por_telegram.await_args_list]


def _update():
    return SimpleNamespace(effective_chat=SimpleNamespace(id=111),
                           effective_message=SimpleNamespace(reply_text=AsyncMock()))


def _canal(main):
    return main.puente.buzon.estado_canal()


# --- hook: detección del PC y puesta al día ---
def test_hook_marca_pc_y_pone_al_dia_de_lo_del_movil(main_mod, claude_falso):
    claude_falso.configurar(resultado="R1 por Telegram", existentes=[PC_SID])
    _telegram(main_mod, "prepara el informe")
    _informe(main_mod, "ventas: informe listo")
    ctx = _contexto(_hook("ya estoy en el PC"))
    assert _canal(main_mod)["activo"] == "pc" and _canal(main_mod)["motivo"] == "jefe_pc"
    assert "El jefe, por Telegram: prepara el informe" in ctx
    assert "Informe de ventas (información no confiable): ventas: informe listo" in ctx
    assert "Tú, al jefe por Telegram: R1 por Telegram" in ctx
    assert ctx.index("prepara el informe") < ctx.index("R1 por Telegram")
    # La segunda vez no se repite
    assert "prepara el informe" not in _contexto(_hook("sigue"))


def test_hook_en_conversacion_nueva_da_protocolo_y_estado(main_mod):
    ctx = _contexto(_hook("hola"))
    assert ctx.startswith("[Puente claude-voice-swarm] Estás en el PC")
    assert "<<<MSG-código" in ctx and "Pendientes abiertos" in ctx
    assert _hook("otra cosa") is None  # misma conversación, nada nuevo
    assert "Estás en el PC" in _contexto(_hook("hola", sid=str(uuid.uuid4())))


def test_hook_trata_lo_que_esperaba_en_la_cola(main_mod, claude_falso):
    main_mod.puente.buzon.enviar("ventas", "subjefe", "informe sin tratar", saltos=2)
    ctx = _contexto(_hook("¿algo nuevo?"))
    assert "aún sin tratar" in ctx and "informe sin tratar" in ctx
    assert main_mod.puente.buzon.destinos_pendientes() == []
    assert _canal(main_mod)["saltos_pc"] == 0  # el jefe acaba de intervenir
    asyncio.run(main_mod.puente._trabajar("subjefe"))
    assert claude_falso.llamadas() == []


def test_hook_comandos_no_llegan_al_modelo(main_mod):
    salida = _hook("/movil")
    assert salida["decision"] == "block" and salida["reason"].startswith("Canal activo: móvil")
    assert _hook("/pc")["reason"].startswith("Canal activo: PC")
    assert _hook("/canal")["reason"].startswith("Canal activo: PC")


def test_hook_ignora_mensajes_de_otra_sesion_y_otras_carpetas(main_mod, tmp_path):
    assert _hook('<cross-session-message from="rele-pc-3">informe</cross-session-message>') is None
    otra = tmp_path / "otra"
    otra.mkdir()
    assert _hook("hola", cwd=otra) is None
    assert _canal(main_mod)["activo"] == "movil"


def test_hook_mantiene_abierto_el_tema(main_mod, claude_falso):
    _telegram(main_mod, "hola")
    tema = main_mod.puente.buzon.tema_abierto()
    _hook("sigo en el PC")
    assert main_mod.puente.buzon.tema_abierto()["ultimo_jefe"] > tema["ultimo_jefe"]


def _correr_hook(env_extra, texto="hola"):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_VOICE_SWARM_PUENTE"}
    env.update(env_extra)
    entrada = json.dumps({"cwd": str(Config.SUBJEFE_DIR), "prompt": texto, "session_id": PC_SID,
                          "transcript_path": "", "hook_event_name": "UserPromptSubmit"})
    return subprocess.run([sys.executable, str(REPO / "hook_subjefe_pc.py"), str(Config.DATA_DIR),
                           str(Config.EMPRESAS_FILE), str(Config.SUBJEFE_DIR)],
                          input=entrada.encode("utf-8"), env=env, capture_output=True, timeout=60)


def test_hook_se_ignora_en_los_turnos_headless(main_mod):
    Config.SUBJEFE_DIR.mkdir(parents=True, exist_ok=True)
    p = _correr_hook({"CLAUDE_VOICE_SWARM_PUENTE": "1"})
    assert p.returncode == 0 and p.stdout == b""
    assert _canal(main_mod)["activo"] == "movil"
    p = _correr_hook({})
    assert p.returncode == 0, p.stderr
    salida = json.loads(p.stdout)
    assert salida["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert _canal(main_mod)["activo"] == "pc" and _canal(main_mod)["pc_session_id"] == PC_SID


def test_hook_nunca_bloquea_por_un_fallo_propio(tmp_path):
    p = subprocess.run([sys.executable, str(REPO / "hook_subjefe_pc.py"), str(tmp_path), "x", "y"],
                       input=b"no es json", capture_output=True, timeout=60,
                       env={k: v for k, v in os.environ.items() if k != "CLAUDE_VOICE_SWARM_PUENTE"})
    assert p.returncode == 0 and p.stdout == b"" and b"hook del canal PC" in p.stderr


# --- informes al PC por el relé ---
def test_informes_con_canal_pc_van_a_la_sesion_del_pc(main_mod, claude_falso):
    _hook("hola")
    _informe(main_mod, "ventas: informe listo", saltos=2)
    assert _turnos(claude_falso) == []  # no hay turno headless
    (rele,) = _reles(claude_falso)
    texto = _texto_rele(rele)
    assert texto.startswith("[Puente claude-voice-swarm] Mensajes para ti, subjefe")
    assert 'origen="empresa:ventas"' in texto and "ventas: informe listo" in texto
    assert "Destinatario: Subjefe" in rele["stdin"]
    assert main_mod.responder_por_telegram.await_count == 0
    assert _canal(main_mod)["saltos_pc"] == 2
    # Lo visto en el PC no se le vuelve a contar al hook
    assert "ventas: informe listo" not in _contexto(_hook("¿y?"))


def test_el_rele_corre_en_bypass_solo_con_dos_herramientas_y_la_guarda(main_mod, claude_falso):
    _hook("hola")
    _informe(main_mod)
    (rele,) = _reles(claude_falso)
    argv = rele["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert argv[argv.index("--tools") + 1] == "ListAgents,SendMessage"
    assert argv[argv.index("--allowedTools") + 1:] == ["ListAgents", "SendMessage"]
    assert argv[argv.index("--setting-sources") + 1] == "project"
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--system-prompt"):
        assert flag in argv
    assert "--mcp-config" not in argv and "ToolSearch" not in " ".join(argv)
    ajustes = json.loads(argv[argv.index("--settings") + 1])
    (grupo,) = ajustes["hooks"]["PreToolUse"]
    assert "guarda_rele.py" in grupo["hooks"][0]["command"]
    assert rele["env"]["RELE_PC_DESTINO"] == "Subjefe"
    assert not os.path.exists(rele["env"]["RELE_PC_ESPERADO"])  # se borra tras la entrega
    assert "TELEGRAM_TOKEN" not in rele["env"]


def test_guarda_del_rele():
    entrada = {"tool_name": "SendMessage", "tool_input": {"to": "Subjefe", "message": "hola\r\n"}}
    assert guarda_rele.decidir(entrada, "Subjefe", "hola") is None
    assert guarda_rele.decidir({**entrada, "tool_input": {"to": "Subjefe [ab12cd]", "message": "hola"}},
                               "Subjefe", "hola") is None
    assert guarda_rele.decidir({"tool_name": "ListAgents", "tool_input": {}}, "Subjefe", "hola") is None
    assert "solo se puede enviar" in guarda_rele.decidir(
        {"tool_name": "SendMessage", "tool_input": {"to": "Otra", "message": "hola"}}, "Subjefe", "hola")
    assert "texto exacto" in guarda_rele.decidir(
        {"tool_name": "SendMessage", "tool_input": {"to": "Subjefe", "message": "hola y algo más"}}, "Subjefe", "hola")
    assert guarda_rele.decidir({"tool_name": "Bash", "tool_input": {}}, "Subjefe", "hola")


def test_guarda_del_rele_falla_cerrada(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("RELE_PC_")}
    entrada = json.dumps({"tool_name": "SendMessage", "tool_input": {"to": "Subjefe", "message": "hola"}})
    p = subprocess.run([sys.executable, str(REPO / "guarda_rele.py")], input=entrada.encode(), env=env,
                       capture_output=True, timeout=60)
    assert p.returncode == 2 and b"Denegado" in p.stderr
    esperado = tmp_path / "e.txt"
    esperado.write_text("hola", encoding="utf-8")
    env.update(RELE_PC_DESTINO="Subjefe", RELE_PC_ESPERADO=str(esperado))
    p = subprocess.run([sys.executable, str(REPO / "guarda_rele.py")], input=entrada.encode(), env=env,
                       capture_output=True, timeout=60)
    assert p.returncode == 0


@pytest.mark.parametrize("modo", ["ausente", "sin_json", "alterado", "otra_sesion"])
def test_si_el_rele_falla_lo_trata_el_headless_y_va_a_telegram(main_mod, claude_falso, modo):
    _hook("hola")
    claude_falso.configurar(resultado="R: el informe está bien", rele=modo, existentes=[PC_SID])
    _informe(main_mod, "ventas: informe listo")
    estado = _canal(main_mod)
    assert (estado["activo"], estado["motivo"]) == ("movil", "fallo_rele")
    (turno,) = _turnos(claude_falso)
    assert "ventas: informe listo" in turno["stdin"]
    (texto,) = _telegramas(main_mod)
    assert texto.startswith("(No pude entregarlo en el PC") and texto.endswith("R: el informe está bien")


# --- paso del PC al móvil: continuidad con --fork-session ---
def test_al_volver_al_movil_el_headless_continua_la_conversacion_del_pc(main_mod, claude_falso):
    _transcripcion_pc(("user", "prepara el plan", time.time() - 60))
    _hook("prepara el plan")
    actividad = _canal(main_mod)["actividad_pc"]
    _transcripcion_pc(("assistant", "Plan encargado a ventas.", actividad + 1))
    claude_falso.configurar(resultado="R por Telegram", existentes=[PC_SID])
    _telegram(main_mod, "ya estoy fuera, ¿cómo va?")
    (turno,) = _turnos(claude_falso)
    argv = turno["argv"]
    assert argv[argv.index("--resume") + 1] == PC_SID and "--fork-session" in argv
    assert "--session-id" not in argv
    copia = main_mod.puente.buzon.info_sesion("subjefe")["session_id"]
    assert copia != PC_SID and main_mod.puente.buzon.info_sesion("subjefe")["iniciada"] == 1
    recopilacion, respuesta = _telegramas(main_mod)
    assert "Mientras no estabas en el PC te dije allí" in recopilacion and "Plan encargado a ventas." in recopilacion
    assert respuesta == "R por Telegram"
    # El siguiente turno sigue en la copia, sin otro fork
    _telegram(main_mod, "gracias")
    argv = _turnos(claude_falso)[1]["argv"]
    assert argv[argv.index("--resume") + 1] == copia and "--fork-session" not in argv


def test_sin_transcripcion_en_la_carpeta_sigue_con_un_extracto(main_mod, claude_falso, tmp_path):
    otra = tmp_path / "app" / f"{PC_SID}.jsonl"
    otra.parent.mkdir(parents=True)
    ahora = time.time()
    otra.write_text(json.dumps({"type": "user", "timestamp": _iso(ahora - 5), "message": {"content": "reserva sala"}})
                    + "\n" + json.dumps({"type": "assistant", "timestamp": _iso(ahora - 4),
                                         "message": {"content": [{"type": "text", "text": "Sala 3 reservada."}]}})
                    + "\n", encoding="utf-8")
    _hook("reserva sala", transcript=otra)
    _telegram(main_mod, "¿qué sala era?")
    (turno,) = _turnos(claude_falso)
    assert "--fork-session" not in turno["argv"]
    assert "El jefe ha estado hablando contigo en el PC" in turno["stdin"]
    assert "jefe: reserva sala" in turno["stdin"] and "tú: Sala 3 reservada." in turno["stdin"]


def test_si_el_fork_falla_sigue_con_un_extracto(main_mod, claude_falso):
    _transcripcion_pc(("user", "reserva sala", time.time() - 5))
    _hook("reserva sala")
    claude_falso.configurar(resultado="R", existentes=[])  # claude no encuentra la conversación del PC
    _telegram(main_mod, "¿qué sala era?")
    fork, turno = _turnos(claude_falso)
    assert "--fork-session" in fork["argv"] and "--fork-session" not in turno["argv"]
    assert "jefe: reserva sala" in turno["stdin"]
    assert _telegramas(main_mod) == ["R"]


def test_si_el_subjefe_del_pc_cerro_el_tema_no_hay_fork(main_mod, claude_falso):
    _telegram(main_mod, "hola")
    _transcripcion_pc(("user", "listo", time.time()))
    _hook("listo, cierra")
    herramientas.cerrar_tema(Buzon(), "Plan entregado.", "")  # lo hace el subjefe del PC con su herramienta
    _telegram(main_mod, "otra cosa")
    turno = _turnos(claude_falso)[1]
    assert "--fork-session" not in turno["argv"] and "--session-id" in turno["argv"]
    assert "Plan entregado." in turno["stdin"]


# --- inactividad ---
def _en_pc(main_mod):
    _hook("hola")
    return _canal(main_mod)["actividad_pc"]


def test_inactividad_de_entrada_pasa_al_movil_con_lo_no_visto(main_mod, monkeypatch):
    actividad = _en_pc(main_mod)
    _transcripcion_pc(("assistant", "Te resumo el informe de ventas.", actividad + 2))
    monkeypatch.setattr(rele_pc, "segundos_inactivo", lambda: 16 * 60 + 1)
    asyncio.run(main_mod.puente.vigilar_canal(actividad + 16 * 60))
    estado = _canal(main_mod)
    assert (estado["activo"], estado["motivo"]) == ("movil", "inactividad")
    (recopilacion,) = _telegramas(main_mod)
    assert "Te resumo el informe de ventas." in recopilacion
    assert main_mod.puente.buzon.info_sesion("subjefe")["fork_de"] == PC_SID


def test_actividad_reciente_mantiene_el_pc(main_mod, monkeypatch):
    actividad = _en_pc(main_mod)
    monkeypatch.setattr(rele_pc, "segundos_inactivo", lambda: 30)
    asyncio.run(main_mod.puente.vigilar_canal(actividad + 16 * 60))
    assert _canal(main_mod)["activo"] == "pc" and _telegramas(main_mod) == []


def test_sin_medida_de_entrada_cuenta_el_ultimo_mensaje_del_pc(main_mod):
    actividad = _en_pc(main_mod)
    asyncio.run(main_mod.puente.vigilar_canal(actividad + 14 * 60))
    assert _canal(main_mod)["activo"] == "pc"
    asyncio.run(main_mod.puente.vigilar_canal(actividad + 15 * 60 + 1))
    assert _canal(main_mod)["activo"] == "movil"


def test_fuente_mensajes_ignora_la_entrada_de_windows(main_mod, monkeypatch):
    monkeypatch.setattr(Config, "PC_INACTIVIDAD_FUENTE", "mensajes")
    monkeypatch.setattr(rele_pc, "segundos_inactivo", lambda: 0)
    actividad = _en_pc(main_mod)
    asyncio.run(main_mod.puente.vigilar_canal(actividad + 15 * 60 + 1))
    assert _canal(main_mod)["activo"] == "movil"


def test_inactividad_cero_es_nunca(main_mod, monkeypatch):
    monkeypatch.setattr(Config, "PC_INACTIVIDAD_MIN", 0)
    actividad = _en_pc(main_mod)
    asyncio.run(main_mod.puente.vigilar_canal(actividad + 10 * 3600))
    assert _canal(main_mod)["activo"] == "pc"


def test_segundos_inactivo_no_falla():
    valor = rele_pc.segundos_inactivo()
    assert valor is None or valor >= 0


# --- comandos desde Telegram ---
def test_comandos_desde_telegram(main_mod, claude_falso):
    upd = _update()
    asyncio.run(main_mod.cmd_pc(upd, None))
    assert "PC" in upd.effective_message.reply_text.await_args.args[0] and _canal(main_mod)["activo"] == "pc"
    upd = _update()
    asyncio.run(main_mod.cmd_canal(upd, None))
    assert upd.effective_message.reply_text.await_args.args[0].startswith("Canal activo: PC")
    asyncio.run(main_mod.cmd_movil(_update(), None))
    assert _canal(main_mod)["activo"] == "movil" and claude_falso.llamadas() == []


def test_el_canal_persiste_tras_reiniciar(main_mod):
    _hook("hola")
    assert Buzon(Config.BUZON_DB, clave=b"otra" * 8).estado_canal()["activo"] == "pc"


def test_canal_pc_desactivado_sin_titulo(main_mod, claude_falso, monkeypatch):
    monkeypatch.setattr(Config, "SUBJEFE_PC_TITULO", "")
    _hook("hola")
    _informe(main_mod)
    assert _reles(claude_falso) == [] and len(_turnos(claude_falso)) == 1


# --- urgencias y voz ---
def test_urgencia_con_canal_movil_no_se_duplica_en_telegram(main_mod, claude_falso):
    claude_falso.configurar(modos=["alerta"], resultado="te he avisado al móvil", existentes=[PC_SID])
    _telegram(main_mod, "vigila el servidor")
    assert _telegramas(main_mod) == ["te he avisado al móvil"] and _reles(claude_falso) == []


def test_lo_del_puente_con_canal_pc_va_al_pc_con_las_urgencias(main_mod, claude_falso):
    _hook("hola")
    asyncio.run(main_mod.puente.al_jefe(["respuesta que estaba en curso"], modo_voz=True,
                                        urgencias=["[URGENTE] Aviso: se cae"]))
    texto = _texto_rele(_reles(claude_falso)[0])
    assert "[URGENTE] Aviso: se cae" in texto and "respuesta que estaba en curso" in texto
    assert main_mod.responder_por_telegram.await_count == 0


def test_voz_solo_en_telegram(main_mod, claude_falso):
    claude_falso.configurar(resultado="respuesta hablada", existentes=[PC_SID])
    _telegram(main_mod, "dime algo", modo_voz=True)
    assert main_mod.responder_por_telegram.await_args.kwargs["modo_voz"] is True
    _hook("hola")
    claude_falso.configurar(resultado="tras el fallo", rele="ausente", existentes=[PC_SID])
    _informe(main_mod)
    assert main_mod.responder_por_telegram.await_args.kwargs["modo_voz"] is False


# --- el subjefe (PC o headless) no es una sesión externa; el headless no carga lo del PC ---
def test_la_carpeta_del_subjefe_no_se_da_de_alta_como_externa(buzon):
    Config.SUBJEFE_DIR.mkdir(parents=True, exist_ok=True)
    out = herramientas.informar_externo(buzon, {}, None, Config.SUBJEFE_DIR, str(uuid.uuid4()), "entorno", "hola")
    assert out.startswith("Aviso no entregado")
    assert buzon.sesiones_registradas() == [] and buzon.destinos_pendientes() == []


def _env_mcp(tmp_path, rol, **extra):
    env = dict(os.environ, ROL=rol, DATA_DIR=str(tmp_path / "datos_mcp"), EMPRESAS_FILE=str(tmp_path / "no.json"),
               SUBJEFE_DIR=str(Config.SUBJEFE_DIR), CLAUDE_CODE_SESSION_ID=str(uuid.uuid4()), PYTHONUTF8="1",
               PYTHONPATH=str(REPO))
    for k in ("CLAUDE_VOICE_SWARM_PUENTE", "CLAUDE_PROJECT_DIR"):
        env.pop(k, None)
    env.update(extra)
    return env


def test_mcp_de_usuario_no_ofrece_informar_en_la_carpeta_del_subjefe(tmp_path):
    pytest.importorskip("mcp")
    Config.SUBJEFE_DIR.mkdir(parents=True, exist_ok=True)
    out = subprocess.run([sys.executable, "-c", "import mcp_server; print(hasattr(mcp_server, 'informar_al_subjefe'))"],
                         cwd=Config.SUBJEFE_DIR, env=_env_mcp(tmp_path, "externa"), capture_output=True, text=True,
                         check=True).stdout
    assert out.strip() == "False"


def test_mcp_subjefe_pc(tmp_path):
    pytest.importorskip("mcp")
    Config.SUBJEFE_DIR.mkdir(parents=True, exist_ok=True)
    datos = tmp_path / "datos_mcp"
    b = Buzon(datos / "buzon.sqlite3")
    b.registrar_sesion("ventas", str(tmp_path), "Ventas", None)
    b.fijar_saltos_pc(3)
    codigo = ("import asyncio, mcp_server as m; print(all(hasattr(m, n) for n in ('encargar', 'crear_sesion', "
              "'cerrar_tema', 'alerta_urgente', 'cambiar_canal', 'ponerse_al_dia'))); "
              "print(asyncio.run(m.encargar('ventas', 'otra vuelta'))); "
              "print(asyncio.run(m.crear_sesion('nueva', 'x', 'd')))")
    out = subprocess.run([sys.executable, "-c", codigo], cwd=Config.SUBJEFE_DIR, env=_env_mcp(tmp_path, "subjefe_pc"),
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    lineas = out.stdout.splitlines()
    assert lineas[0] == "True" and "Límite de encargos encadenados" in lineas[1] and lineas[2].startswith("No creada")
    for env in (_env_mcp(tmp_path, "subjefe_pc", CLAUDE_VOICE_SWARM_PUENTE="1"),):
        p = subprocess.run([sys.executable, "-c", "import mcp_server"], cwd=Config.SUBJEFE_DIR, env=env,
                           capture_output=True, text=True, timeout=60)
        assert p.returncode != 0 and "subjefe_pc" in p.stderr
    p = subprocess.run([sys.executable, "-c", "import mcp_server"], cwd=tmp_path, env=_env_mcp(tmp_path, "subjefe_pc"),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode != 0


def test_el_headless_no_carga_el_mcp_del_pc(main_mod, claude_falso):
    sesiones.preparar_carpeta_subjefe_pc()
    assert (Config.SUBJEFE_DIR / ".mcp.json").exists()
    _telegram(main_mod, "hola")
    (turno,) = _turnos(claude_falso)
    argv = turno["argv"]
    assert "--strict-mcp-config" in argv
    conf = json.loads(open(argv[argv.index("--mcp-config") + 1], encoding="utf-8").read())
    assert conf["mcpServers"]["buzon"]["env"]["ROL"] == "subjefe"
    assert turno["env"]["CLAUDE_VOICE_SWARM_PUENTE"] == "1"  # el hook se sale en este turno


def test_preparar_carpeta_del_subjefe_pc():
    d = Config.SUBJEFE_DIR
    (d / ".claude" / "commands").mkdir(parents=True)
    (d / ".claude" / "commands" / "pc.md").write_text("mío", encoding="utf-8")
    (d / ".mcp.json").write_text(json.dumps({"mcpServers": {"otro": {"command": "x"}}}), encoding="utf-8")
    (d / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {"allow": ["Read"]}, "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
                                                                                         "command": "otro"}]}]}}),
        encoding="utf-8")
    sesiones.preparar_carpeta_subjefe_pc()
    sesiones.preparar_carpeta_subjefe_pc()  # idempotente
    mcp = json.loads((d / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert mcp["otro"] == {"command": "x"} and mcp["buzon"]["env"]["ROL"] == "subjefe_pc"
    ajustes = json.loads((d / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert ajustes["permissions"] == {"allow": ["Read"]} and ajustes["enabledMcpjsonServers"] == ["buzon"]
    grupos = ajustes["hooks"]["UserPromptSubmit"]
    assert len(grupos) == 2 and grupos[0]["hooks"][0]["command"] == "otro"
    comando = grupos[1]["hooks"][0]["command"]
    assert "hook_subjefe_pc.py" in comando and "\\" not in comando
    assert (d / ".claude" / "commands" / "pc.md").read_text(encoding="utf-8") == "mío"
    assert "cambiar_canal" in (d / ".claude" / "commands" / "movil.md").read_text(encoding="utf-8")


# --- verificación de la salida del relé ---
def _lineas(to="Subjefe", message="hola", resultado=None, extra=None):
    resultado = resultado or {"type": "tool_result", "tool_use_id": "t1", "content": "Message sent."}
    usuario = {"type": "user", "message": {"content": [resultado]}}
    if extra is not None:
        usuario["tool_use_result"] = extra
    return [json.dumps(x) for x in (
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "SendMessage", "input": {"to": to, "message": message}}]}},
        usuario, {"type": "result", "result": '{"ok": true}', "total_cost_usd": 0.004})]


def test_analizar_salida_del_rele():
    assert rele_pc.analizar_salida(_lineas(), "hola", "Subjefe").ok
    assert rele_pc.analizar_salida(_lineas(to="Subjefe [614214]"), "hola", "Subjefe").ok
    assert rele_pc.analizar_salida(_lineas(message="hola\r\n"), "hola", "Subjefe").ok
    assert rele_pc.analizar_salida(_lineas(resultado={"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                                           extra={"success": True}), "hola", "Subjefe").ok
    # Formato real de un fallo (18/10/2026): tool_use_result {"success": false, ...}
    real = {"type": "tool_result", "tool_use_id": "t1",
            "content": [{"type": "text", "text": '{"success":false,"message":"No agent named \'Subjefe\' is reachable."}'}]}
    assert not rele_pc.analizar_salida(_lineas(resultado=real, extra={"success": False}), "hola", "Subjefe").ok
    assert not rele_pc.analizar_salida(_lineas(resultado=real), "hola", "Subjefe").ok
    r = rele_pc.analizar_salida(_lineas(message="hola, resumido"), "hola", "Subjefe")
    assert not r.ok and "alteró" in r.motivo and r.coste_usd == 0.004
    assert not rele_pc.analizar_salida(_lineas(to="Otra"), "hola", "Subjefe").ok
    solo_texto = [json.dumps({"type": "result", "result": '{"ok": true}', "total_cost_usd": 0.001})]
    assert not rele_pc.analizar_salida(solo_texto, "hola", "Subjefe").ok


def test_leer_transcripcion_omite_herramientas_y_otras_sesiones(tmp_path):
    t = tmp_path / "t.jsonl"
    ahora = time.time()
    filas = [
        {"type": "user", "timestamp": _iso(ahora - 3), "message": {"content": "hola"}},
        {"type": "user", "timestamp": _iso(ahora - 2), "message": {"content": [{"type": "tool_result", "content": "x"}]}},
        {"type": "user", "timestamp": _iso(ahora - 2), "message": {"content": "<cross-session-message from=\"a\">x"}},
        {"type": "assistant", "timestamp": _iso(ahora - 1), "message": {"content": [{"type": "text", "text": "adiós"}]}},
    ]
    t.write_text("\n".join(json.dumps(f) for f in filas) + "\nbasura\n", encoding="utf-8")
    assert [(r, x) for _, r, x in rele_pc.leer_transcripcion(t)] == [("user", "hola"), ("assistant", "adiós")]
    assert [x for _, _, x in rele_pc.leer_transcripcion(t, desde=ahora - 1.5)] == ["adiós"]
    assert rele_pc.leer_transcripcion(tmp_path / "no.jsonl") == []
